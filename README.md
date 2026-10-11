# CALIGA

Internal Wavelynx tool for managing reader configuration profiles, running firmware validation tests, remediating failures, and building and flashing reader binaries.

## Requirements

- Python 3.14
- Access to the CALIGA PostgreSQL database
- Google Cloud access to the config bucket (`gcloud auth application-default login`)
- For flashing: Chrome or Edge, with an RS-485 USB adapter plugged into your computer

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in the values
```

`SECRET_KEY` and `DATABASE_URL` are required. The app won't start without them. Generate a secret key with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Database

Schema changes are managed with Flask-Migrate (Alembic). Migration files live in `migrations/versions/`.

**Existing database** (already has the tables): mark it as being at the baseline once, then upgrade.

```bash
.venv/bin/flask --app app db stamp 0001_baseline
.venv/bin/flask --app app db upgrade
```

**New, empty database:**

```bash
.venv/bin/flask --app app db upgrade
.venv/bin/python init_db.py    # seeds the team users
```

Before running `upgrade` against a shared database, preview the SQL with `flask --app app db upgrade --sql`, and back the database up.

The `0002_review_fixes` migration converts `config_id_dec` to an integer. It stops with a list of rows if any value isn't a whole number. Fix those rows and run it again. Config IDs aren't required to be unique, because some existing configurations share an ID (CBA6 and CAM3 are both 8). New IDs are allocated one at a time under a database lock so new duplicates can't happen.

To change the schema later: edit `models.py`, then run `flask --app app db migrate -m "describe the change"`, review the generated file, and run `flask --app app db upgrade`.

## Running

```bash
.venv/bin/python app.py
```

Open http://127.0.0.1:5000 and pick a user from the menu at the top right. Set `FLASK_DEBUG=1` in `.env` for the debugger and auto-reload on your own machine only.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Tests use a throwaway SQLite database and never contact Slack, Jira, Forge, GCS, or PocketBase. Credentials are blanked and the service calls are stubbed.

| File | Covers |
|---|---|
| `tests/test_auth_and_pages.py` | User switching, role gates, CSRF, every page for every role |
| `tests/test_configurations.py` | Create, edit, toggle, archive, alert QA, LEAF and wallet fields |
| `tests/test_testing_workflow.py` | Firmware runs, submit rules, promotion and demotion |
| `tests/test_failures.py` | Claim, re-test, lazy INI, edited-INI firmware builds |
| `tests/test_tools_and_jobs.py` | Background job tools, flash logging, YAML automator |
| `tests/test_forge_and_csn.py` | IAP cookie handling, partial config CSN output |
| `tests/test_ini_generation.py` | INI-Generator form rows, master INI backfill, generated INI contents |
| `tests/test_users_admin.py` | Sign-in rules, the admin Users page, deactivation, sign out |
| `tests/test_job_queue_processes.py` | Jobs queued in one process run once in separate worker processes |
| `tests/test_osdp_flasher_js.py` | The browser flasher matches recorded OSDP reference frames byte for byte, and handles a simulated reader (needs Node.js) |

One test needs INI-Generator's private files and is skipped without them:

```bash
CALIGA_TEST_INI_PRIVATE_DIR=~/Desktop/INI-Generator/private .venv/bin/python -m pytest
```

## Sign-in and users

`AUTH_MODE` picks how people sign in. Only `dev` exists today: the user menu lets anyone act as any active user (`/switch_user/<role>?email=<email>`), and every page shows a warning banner. Use it only on your own machine. Google sign-in or Google IAP will be added as further modes before CALIGA is shared or hosted. They'll sign people in through the same `find_login_user` and `login_user` functions in `auth.py`, so roles and access rules stay the same.

- Only people in the users table who are active can use CALIGA. Anyone else is blocked, and only @wavelynx.com addresses are allowed (`ALLOWED_EMAIL_DOMAIN`).
- Admins manage users on **Manage Users** (`/admin/users`): add people, change roles, deactivate or reactivate accounts. James H. is the only admin. Admin rights aren't editable in the app.
- A deactivated user is signed out on their next request.
- Sign-ins last `SESSION_HOURS` (default 12).

## Roles

| Role  | Can do |
|-------|--------|
| Sales | Create configurations, edit POC configurations, move between POC and Active, archive, alert QA |
| QA    | Start firmware runs, assign test groups, submit results, flash readers |
| SET   | Everything QA can do, plus the Failures dashboard: claim, edit INI, flash with edited INI, re-test. Can alert QA. |

Revision history is visible to Sales and SET.

## Configuration lifecycle

`POC` → `Active` (Sales) → `Production` (passes every criterion in a firmware run). A Production config that fails a later run goes back to its previous status. `Archived` disables editing and tools.

Only Active and Production configurations can be added to a firmware run.

## Project layout

```
app.py                 App factory, error handling, blueprint registration
auth.py                Current user and @require_role
jobs.py                Shared background job queue (jobs table + worker threads)
models.py              SQLAlchemy models
routes/                Blueprints: main, configurations, testing, failures, tools, jobs
services/              Jira, Slack, Forge, GCS, PocketBase, OSDP, INI generation and translation
utils/                 Firmware version, CSN, and error helpers
static/js/osdp-flasher.js  Browser OSDP flashing over Web Serial
tools/                 One-off maintenance scripts
templates/             Jinja templates (Bootstrap 5)
migrations/            Alembic migrations
```

## Background jobs

Forge builds and PocketBase syncs can take a minute or more. These requests add a row to the `jobs` table and return right away. Worker threads claim pending jobs, run them, and save progress, results, and output files. The page polls `/jobs/<id>`, and any CALIGA process can answer, so this works with several server processes or machines.

- Each web process starts `JOB_WORKERS` worker threads (default 2) with its first request.
- For a dedicated worker, run `flask --app app run-worker` (`JOB_WORKER_THREADS`, default 4) and set `JOB_WORKERS=0` on the web processes.
- Job files (built binaries, generated INIs) are stored in the database (`job_files`), never in GCS.
- A running job whose worker stops sending heartbeats for 10 minutes is marked failed. Finished jobs and their files are deleted after a day.

## External services

| Service | Module | Notes |
|---------|--------|-------|
| Jira | `services/jira_service.py` | Creates a bug on test failure, assigns on claim, moves to Complete on re-test |
| Slack | `services/slack_service.py` | Webhook alerts for QA requests, failures and re-tests |
| Forge | `services/forge_service.py` | Builds profile and firmware binaries. Authenticates with a browser IAP cookie that expires. Refresh `ACTIVE_IAP_COOKIE` when builds start failing. |
| GCS | `services/gcs_service.py` | Reads master INIs in `input/` and build output in `output/`. Writes only in `forge/jhepburn@wavelynx.com/` (`FORGE_FOLDER_EMAIL`), whoever is signed in. |
| PocketBase | `services/pocketbase_service.py` | Token lookup (read-only) and sync (uploads binaries) |
| OSDP | `static/js/osdp-flasher.js` | Runs in the browser over Web Serial. Finds the reader, raises baud to 115200, and transfers firmware. |

## INI generation

When a configuration has no master INI in `input/<version>/` (or in the Forge folder, `forge/jhepburn@wavelynx.com/`), CALIGA generates one from the database with [INI-Generator](https://github.com/jhepburn-hue/INI-Generator). It's installed as a package pinned to a commit in `requirements.txt`. To pick up INI-Generator changes, update that commit and reinstall.

`services/ini_generator.py` turns the configuration's fields into INI-Generator's form rows. INI-Generator applies `settings_default.ini` and the customer profiles, and lists anything it couldn't decide. Those warnings are written as comments at the top of the generated INI.

INI-Generator needs `settings_default.ini` and `customer_profiles.json`. CALIGA reads them from the local folder in `INI_GENERATOR_PRIVATE_DIR` (for example your INI-Generator clone's `private/` folder). They're never stored in GCS. Restart CALIGA after updating either file.

The LEAF & Wallet fields (Leaf Si/Cc, Keyset ID, Custom Keyset, ECP DESFire, MiFare2Go, ECP TCI, Meridian bit count) and BLE Advertising Name feed INI-Generator directly. Blank fields leave those settings at defaults.

`tools/backfill_ini_fields.py` fills those fields from each configuration's master INI in `input/<version>/`. It only fills blank fields and adds a revision history entry for each configuration it changes. Run it without arguments for a dry run, and with `--apply` to save.

## Flashing

Flashing runs in your browser, through the RS-485 adapter plugged into your computer, so several people can flash their own readers from one shared CALIGA. It needs Chrome or Edge (Web Serial), and the site must be served over HTTPS or from localhost.

1. Click a Flash button and pick the adapter in the browser prompt. After the first time, the browser remembers it.
2. The server finds or builds the firmware as a job.
3. The browser downloads it and runs the OSDP steps from `static/js/osdp-flasher.js`: find the reader (baud rates and addresses 0-7), raise the baud rate to 115200, check osdp_ID and osdp_POLL, then send the file in fragments until the reader reports it's rebooting.
4. The result is saved to the configuration's revision history.

On the Failures dashboard, **Flash Reader** saves the edited INI and forces a fresh Forge build from it before flashing. It stops at the first step that fails.

