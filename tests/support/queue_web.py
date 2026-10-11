"""
Web side of the job queue test: queues jobs with no workers of its own, while two separate
worker processes run them. Prints a JSON summary.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_env  # noqa: E402

os.environ["JOB_WORKERS"] = "0"  # this process only serves web requests

from app import app  # noqa: E402
from models import db, User, Configuration, Job  # noqa: E402

app.config.update(WTF_CSRF_ENABLED=False)
with app.app_context():
    db.create_all()
    db.session.add_all([User(first_name="J", last_name_initial="H", email="s@x.com", role="SET"),
                        Configuration(config_name="CQU1", config_id_dec=1, status="Active")])
    db.session.commit()

worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue_worker.py")
workers = [subprocess.Popen([sys.executable, worker_script, queue_env.ROOT, queue_env.SCRATCH, "30"]) for _ in range(2)]
try:
    client = app.test_client()
    client.get("/switch_user/SET")
    headers = {"Accept": "application/json"}
    ids = [client.post("/configurations/CQU1/download-profile-bin", headers=headers).json["job_id"] for _ in range(8)]

    deadline = time.time() + 30
    while time.time() < deadline:
        if all(client.get(f"/jobs/{i}", headers=headers).json["status"] in ("done", "failed") for i in ids):
            break
        time.sleep(0.3)

    with app.app_context():
        rows = Job.query.filter(Job.id.in_(ids)).all()
        summary = {
            "jobs": len(ids),
            "rows": len(rows),
            "statuses": sorted(r.status for r in rows),
            "worker_processes": sorted({r.worker for r in rows if r.worker}),
            "files_served": sum(client.get(f"/jobs/{i}/files/bin").data.startswith(b"built by") for i in ids),
        }
    print(json.dumps(summary))
finally:
    for w in workers:
        w.terminate()
