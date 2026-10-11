"""Shared settings for the multi-process job queue test. Usage: <script> <repo root> <scratch dir> ..."""
import os
import sys

ROOT, SCRATCH = sys.argv[1], sys.argv[2]
for key in ["SLACK_WEBHOOK_URL", "JIRA_USER_EMAIL", "JIRA_API_TOKEN", "ACTIVE_IAP_COOKIE", "PB_ADMIN_TOKEN",
            "GOOGLE_APPLICATION_CREDENTIALS"]:
    os.environ[key] = ""
os.environ.update(SECRET_KEY="test-secret", DATABASE_URL=f"sqlite:///{SCRATCH}/queue.db",
                  JOB_STORAGE_DIR=f"{SCRATCH}/job_files", LOG_LEVEL="ERROR")
sys.path.insert(0, ROOT)
os.chdir(ROOT)
