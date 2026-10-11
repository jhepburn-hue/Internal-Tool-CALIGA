"""
Shared background job queue for slow work (Forge builds, PocketBase syncs).

A request enqueues a job (a row in the jobs table) and returns its ID right away. Worker
threads in any CALIGA process claim pending jobs, run them, and record progress, results, and
output files. The page polls /jobs/<id>, and any process can answer because everything, including
the files, lives in the database. Jobs never write to GCS themselves.

Workers start with the first web request (JOB_WORKERS threads per process, default 2). For a
dedicated worker process, run `flask --app app run-worker` and set JOB_WORKERS=0 on the web
processes.
"""
import os
import time
import uuid
import shutil
import socket
import logging
import tempfile
import threading
from datetime import timedelta
from sqlalchemy import update
from models import db, Job, JobFile, utcnow
from utils.errors import UserFacingError

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 30
STALE_AFTER = timedelta(minutes=10)     # A running job with no heartbeat for this long is marked failed.
KEEP_FINISHED_FOR = timedelta(days=1)   # Finished jobs and their files are deleted after this.
PROGRESS_WRITE_SECONDS = 1.0            # Progress is written at most this often.
REAP_SECONDS = 60

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

_handlers = {}
_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


class JobError(UserFacingError):
    """Raise inside a job handler to fail the job with a user-facing message."""


def job_handler(kind):
    """Registers fn(job, **params) as the handler for jobs of this kind."""
    def register(fn):
        _handlers[kind] = fn
        return fn
    return register


def enqueue(kind, owner_id, **params):
    """Queues a job and returns its ID. params must be JSON-serializable."""
    if kind not in _handlers:
        raise ValueError(f"No job handler registered for '{kind}'")
    job = Job(id=uuid.uuid4().hex, kind=kind, owner_id=owner_id, status='pending',
              message='Queued...', params=params)
    db.session.add(job)
    db.session.commit()
    _wake.set()
    return job.id


def get_job(job_id, owner_id):
    """Returns the job if it exists and belongs to owner_id, else None."""
    job = db.session.get(Job, job_id)
    return job if job and job.owner_id == owner_id else None


def read_job_file(job, key):
    """Returns (bytes, download_name) for one of the job's files, or None."""
    stored = JobFile.query.filter_by(job_id=job.id, key=key).first()
    return (stored.data, stored.name) if stored else None


class JobContext:
    """What a handler sees: params, a scratch folder, and ways to report progress and files."""

    def __init__(self, job_id, engine):
        self.id = job_id
        self.workdir = tempfile.mkdtemp(prefix=f"caliga_job_{job_id[:8]}_")
        self.result = {}
        self._engine = engine
        self._files = {}
        self._message = None
        self._progress = None
        self._last_write = 0.0

    def _write(self, force=False, **values):
        # A separate connection, so progress is visible right away without committing the handler's own work.
        now = time.monotonic()
        if not force and now - self._last_write < PROGRESS_WRITE_SECONDS:
            return
        self._last_write = now
        values.setdefault("message", self._message)
        values.setdefault("progress", self._progress)
        with self._engine.begin() as conn:
            conn.execute(update(Job.__table__).where(Job.__table__.c.id == self.id)
                         .values(heartbeat_at=utcnow(), **values))

    @property
    def message(self):
        return self._message

    @message.setter
    def message(self, text):
        self._message = text
        self._write(force=True)

    @property
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, percent):
        self._progress = percent
        self._write()

    def heartbeat(self):
        self._write(force=True)

    def add_file(self, key, local_path, download_name):
        with open(local_path, 'rb') as f:
            data = f.read()
        with self._engine.begin() as conn:
            conn.execute(JobFile.__table__.insert().values(job_id=self.id, key=key, name=download_name,
                                                           data=data, created_at=utcnow()))
        self._files[key] = {"name": download_name}


def _claim_next(app):
    """Claims the oldest pending job for this worker. Safe when several processes claim at once."""
    with app.app_context():
        candidate = (Job.query.filter_by(status='pending').order_by(Job.created_at)
                     .with_for_update(skip_locked=True).first())
        if candidate is None:
            db.session.rollback()
            return None
        job_id = candidate.id
        now = utcnow()
        claimed = db.session.execute(
            update(Job.__table__)
            .where(Job.__table__.c.id == job_id, Job.__table__.c.status == 'pending')
            .values(status='running', worker=WORKER_ID, started_at=now, heartbeat_at=now, message='Starting...')
        ).rowcount
        db.session.commit()
        return job_id if claimed == 1 else None


def _run(app, job_id):
    with app.app_context():
        job = db.session.get(Job, job_id)
        kind = job.kind
        handler = _handlers.get(kind)
        params = dict(job.params or {})
        db.session.commit()

        ctx = JobContext(job_id, db.engine)
        stop_heartbeat = threading.Event()

        def beat():
            while not stop_heartbeat.wait(HEARTBEAT_SECONDS):
                try:
                    ctx.heartbeat()
                except Exception as e:
                    log.warning("Heartbeat for job %s failed: %s", job_id, e)

        threading.Thread(target=beat, daemon=True).start()

        status, error = 'done', None
        try:
            if handler is None:
                raise JobError(f"No handler for job kind '{kind}'.")
            handler(ctx, **params)
        except UserFacingError as e:
            status, error = 'failed', str(e)
            db.session.rollback()
        except Exception as e:
            log.exception("Job %s (%s) crashed: %s", job_id, kind, e)
            status, error = 'failed', f"Unexpected error: {e}"
            db.session.rollback()
        finally:
            stop_heartbeat.set()
            shutil.rmtree(ctx.workdir, ignore_errors=True)

        db.session.execute(
            update(Job.__table__).where(Job.__table__.c.id == job_id).values(
                status=status, error=error, result=ctx.result, files=ctx._files,
                message=ctx.message, progress=ctx.progress, finished_at=utcnow(), heartbeat_at=utcnow()
            )
        )
        db.session.commit()


def _reap(app):
    """Fails jobs whose worker stopped, and deletes old finished jobs and their files."""
    with app.app_context():
        now = utcnow()
        stale = Job.query.filter(Job.status == 'running', Job.heartbeat_at < now - STALE_AFTER).all()
        for job in stale:
            job.status = 'failed'
            job.error = "The server running this job stopped before it finished. Please try again."
            job.finished_at = now

        old = Job.query.filter(Job.finished_at.isnot(None), Job.finished_at < now - KEEP_FINISHED_FOR).all()
        for job in old:
            db.session.delete(job)  # its job_files rows go with it
        db.session.commit()


def _worker_loop(app):
    last_reap = 0.0
    while True:
        try:
            if time.monotonic() - last_reap > REAP_SECONDS:
                last_reap = time.monotonic()
                _reap(app)
            job_id = _claim_next(app)
            if job_id:
                _run(app, job_id)
                continue
        except Exception as e:
            log.exception("Job worker error: %s", e)
            time.sleep(5)
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def start_workers(app, count=None):
    """Starts worker threads in this process, once."""
    global _started
    count = int(os.getenv('JOB_WORKERS', '2')) if count is None else count
    with _start_lock:
        if _started or count <= 0:
            return
        _started = True
    for i in range(count):
        threading.Thread(target=_worker_loop, args=(app,), daemon=True, name=f"caliga-job-worker-{i}").start()
    log.info("Started %d job worker thread(s) (%s).", count, WORKER_ID)
