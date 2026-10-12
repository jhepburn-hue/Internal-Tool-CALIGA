"""
Clears data left over from a test pass: all revision history, all firmware runs (with their test
groups and test cases), all background jobs and their files, the test user, and CTEST configurations.

Real configurations and team users are kept. Jira tickets and Slack messages aren't touched.

    python tools/reset_test_data.py            # dry run: shows what would be deleted
    python tools/reset_test_data.py --apply    # deletes it, in one transaction
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("JOB_WORKERS", "0")

from app import app  # noqa: E402
from models import (db, User, Configuration, RevisionHistory, FWRun, ConfigurationTestGroup,  # noqa: E402
                    TestCase, Job, JobFile)

TEST_USER_EMAILS = ["test.user@wavelynx.com"]
TEST_CONFIG_PREFIX = "CTEST"


def main(apply):
    test_configs = Configuration.query.filter(Configuration.config_name.like(f"{TEST_CONFIG_PREFIX}%"))
    test_users = User.query.filter(User.email.in_(TEST_USER_EMAILS))

    # Children before parents, so foreign keys are never left pointing at deleted rows.
    steps = [
        ("revision history entries (all)", RevisionHistory.query),
        ("test cases", TestCase.query),
        ("test groups", ConfigurationTestGroup.query),
        ("firmware runs", FWRun.query),
        ("job files", JobFile.query),
        ("background jobs", Job.query),
        (f"test configurations ({', '.join(c.config_name for c in test_configs) or 'none'})", test_configs),
        (f"test users ({', '.join(u.email for u in test_users) or 'none'})", test_users),
    ]

    for label, query in steps:
        count = query.count()
        print(f"{'Deleting' if apply else 'Would delete'} {count:4d} {label}")
        if apply and count:
            query.delete(synchronize_session=False)

    if apply:
        db.session.commit()
        print("\nDone.")
    else:
        print("\nDry run only. Run again with --apply to delete.")


if __name__ == "__main__":
    with app.app_context():
        main(apply="--apply" in sys.argv)
