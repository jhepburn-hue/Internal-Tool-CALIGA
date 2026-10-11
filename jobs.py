"""
In-process background jobs for slow work (Forge builds, PocketBase syncs, OSDP flashing).

A request starts a job and returns its ID right away. The page polls /jobs/<id> for
status and progress, then downloads any files the job produced.

Jobs live in this process's memory, so they're lost on restart and aren't shared
between multiple server processes. That fits CALIGA running as one local Flask process.
"""
import time
import uuid
import shutil
import logging
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import current_app

log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 60 * 60

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="caliga-job")
_jobs = {}
_jobs_lock = threading.Lock()


class JobError(Exception):
    """Raise inside a job function to fail the job with a user-facing message."""


class Job:
    def __init__(self, kind, owner_id):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.owner_id = owner_id
        self.status = 'pending'  # pending, running, done, failed
        self.message = 'Queued...'
        self.progress = None
        self.result = {}
        self.files = {}  # key -> (local_path, download_name)
        self.error = None
        self.created_at = time.time()
        self.finished_at = None
        self.workdir = tempfile.mkdtemp(prefix=f"caliga_{kind}_")

    def add_file(self, key, path, download_name):
        self.files[key] = (path, download_name)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "result": self.result,
            "files": sorted(self.files),
            "error": self.error,
        }


def _purge_expired():
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        expired = [j for j in _jobs.values() if j.finished_at and j.finished_at < cutoff]
        for job in expired:
            del _jobs[job.id]
    for job in expired:
        shutil.rmtree(job.workdir, ignore_errors=True)


def start_job(kind, owner_id, fn, *args, **kwargs):
    """Runs fn(job, *args, **kwargs) in the background inside an app context and returns the Job."""
    _purge_expired()
    job = Job(kind, owner_id)
    with _jobs_lock:
        _jobs[job.id] = job

    app = current_app._get_current_object()

    def run():
        with app.app_context():
            job.status = 'running'
            try:
                fn(job, *args, **kwargs)
                job.status = 'done'
            except JobError as e:
                job.status = 'failed'
                job.error = str(e)
            except Exception as e:
                log.exception("Job %s (%s) crashed: %s", job.id, kind, e)
                job.status = 'failed'
                job.error = f"Unexpected error: {e}"
            finally:
                job.finished_at = time.time()

    _executor.submit(run)
    return job


def get_job(job_id, owner_id):
    """Returns the job if it exists and belongs to owner_id, else None."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None or job.owner_id != owner_id:
        return None
    return job
