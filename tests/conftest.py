"""
Shared test setup. Tests run against a throwaway SQLite database and a local job-file folder.

External services are never contacted: their credentials are blanked before the app is
imported, and tests stub the service functions they exercise.

INI generation tests need INI-Generator's private files. Set CALIGA_TEST_INI_PRIVATE_DIR to the
folder with settings_default.ini and customer_profiles.json to run them. Otherwise they're skipped.
"""
import os
import re
import sys
import time
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="caliga_tests_")
INI_PRIVATE_DIR = os.environ.get("CALIGA_TEST_INI_PRIVATE_DIR", "")

# Blank values stop load_dotenv from loading real credentials from .env.
for key in ["SLACK_WEBHOOK_URL", "JIRA_USER_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY", "ACTIVE_IAP_COOKIE",
            "PB_ADMIN_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "FORGE_BASE_URL"]:
    os.environ[key] = ""
os.environ.update(
    SECRET_KEY="test-secret",
    DATABASE_URL=f"sqlite:///{TMP}/test.db",
    PB_BASE_URL="http://127.0.0.1:9",
    INI_GENERATOR_PRIVATE_DIR=INI_PRIVATE_DIR,
    LOG_LEVEL="ERROR",
)

from app import app as flask_app  # noqa: E402
from models import db, User, Configuration  # noqa: E402

JSON = {"Accept": "application/json"}

# (first name, last initial, email, role, is_admin). The first SET user is the admin, like James H.
USERS = [
    ("James", "H", "set@x.com", "SET", True),
    ("Dennis", "P", "set2@x.com", "SET", False),
    ("Rebecca", "R", "qa@x.com", "QA", False),
    ("Jake", "S", "qa2@x.com", "QA", False),
    ("Anthony", "K", "sales@x.com", "Sales", False),
]


@pytest.fixture(scope="session")
def app():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        db.create_all()
    yield flask_app


@pytest.fixture(autouse=True)
def fresh_data(app):
    """Every test starts with only the test users."""
    with app.app_context():
        db.session.remove()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.add_all(User(first_name=f, last_name_initial=l, email=e, role=r, is_admin=a) for f, l, e, r, a in USERS)
        db.session.commit()
    yield


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    """login("QA") or login("QA", "qa2@x.com") switches the test client's user."""
    def switch(role, email=None):
        return client.get(f"/switch_user/{role}" + (f"?email={email}" if email else ""))
    return switch


@pytest.fixture
def make_config(app):
    def create(name, status="Active", config_id=None, **fields):
        with app.app_context():
            next_id = config_id or (db.session.query(db.func.max(Configuration.config_id_dec)).scalar() or 0) + 1
            db.session.add(Configuration(config_name=name, config_id_dec=next_id, config_id_hex=f"0x{next_id:04X}",
                                         status=status, **fields))
            db.session.commit()
        return name
    return create


@pytest.fixture
def wait_for_job(client):
    def wait(job_id, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = client.get(f"/jobs/{job_id}", headers=JSON).json
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.1)
        raise AssertionError(f"Job {job_id} didn't finish within {timeout}s: {job}")
    return wait


def flashes(html):
    """Returns [(category, message)] for the flash messages on a rendered page."""
    return re.findall(r'alert alert-(\w+) alert-dismissible[^>]*>\s*(.*?)\s*<button', html, re.S)


def flashed(response, text):
    return any(text in message for _, message in flashes(response.text))
