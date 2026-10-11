"""The job queue works across processes: jobs queued by one process run elsewhere, exactly once."""
import json
import os
import subprocess
import sys

from conftest import ROOT


def test_jobs_run_once_across_separate_worker_processes(tmp_path):
    script = os.path.join(ROOT, "tests", "support", "queue_web.py")
    result = subprocess.run([sys.executable, script, ROOT, str(tmp_path)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-2000:]
    summary = json.loads(result.stdout.strip().splitlines()[-1])

    assert summary["rows"] == summary["jobs"] == 8
    assert summary["statuses"] == ["done"] * 8                 # every job ran, and none twice
    assert len(summary["worker_processes"]) >= 1              # run by the worker processes, not the web process
    assert summary["files_served"] == 8                       # the web process served files built elsewhere
