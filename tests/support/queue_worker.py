"""A separate worker process for the job queue test. Runs 2 worker threads with a stubbed build."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_env  # noqa: F401,E402

from app import app  # noqa: E402
import jobs  # noqa: E402
import routes.tools as tools  # noqa: E402


def fake_build(config, fw, email, workdir, **kw):
    time.sleep(0.3)  # long enough that both processes are busy at once
    path = os.path.join(workdir, "x.bin")
    open(path, "w").write(f"built by {jobs.WORKER_ID}")
    return path


tools.get_or_build_profile_bin = fake_build
jobs.start_workers(app, 2)
time.sleep(float(sys.argv[3]))
