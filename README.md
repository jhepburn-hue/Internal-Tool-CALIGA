# CALIGA

Internal Wavelynx tool for managing reader configuration profiles, running firmware validation tests, remediating failures, and building and flashing reader binaries.

## Requirements

- Python 3.14
- Access to the CALIGA PostgreSQL database
- Google Cloud access to the config bucket (`gcloud auth application-default login`)
- For flashing: an RS-485 USB adapter connected to the machine running CALIGA

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

## Roles

There's no login yet. The user menu calls `/switch_user/<role>?email=<email>` to act as a team member.

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
jobs.py                In-process background jobs for builds, syncs and flashing
models.py              SQLAlchemy models
routes/                Blueprints: main, configurations, testing, failures, tools, jobs
services/              Jira, Slack, Forge, GCS, PocketBase, OSDP, INI generation and translation
osdp_library/          Vendored OSDP protocol library
utils/                 Prox filter math, firmware version helpers
templates/             Jinja templates (Bootstrap 5)
migrations/            Alembic migrations
```

## Background jobs

Forge builds, PocketBase syncs and flashing can take a minute or more. These requests start a job and return right away. The page polls `/jobs/<id>` for progress and downloads the result when it's ready.

Jobs are kept in memory for one hour. They're lost if the server restarts, and they only work with a single server process, which is how CALIGA runs today.

## External services

| Service | Module | Notes |
|---------|--------|-------|
| Jira | `services/jira_service.py` | Creates a bug on test failure, assigns on claim, moves to Complete on re-test |
| Slack | `services/slack_service.py` | Webhook alerts for QA requests, failures and re-tests |
| Forge | `services/forge_service.py` | Builds profile and firmware binaries. Authenticates with a browser IAP cookie that expires. Refresh `ACTIVE_IAP_COOKIE` when builds start failing. |
| GCS | `services/gcs_service.py` | Master INIs in `input/`, build output in `output/` and `forge/<email>/<version>/` |
| PocketBase | `services/pocketbase_service.py` | Token lookup (read-only) and sync (uploads binaries) |
| OSDP | `services/osdp_service.py` | Finds the reader, raises baud to 115200, and transfers firmware. One flash at a time. |

## INI generation

When a configuration has no master INI in `input/<version>/` (or in your `forge/<email>/` folder), CALIGA generates one from the database with [INI-Generator](https://github.com/jhepburn-hue/INI-Generator). It's installed as a package pinned to a commit in `requirements.txt`. To pick up INI-Generator changes, update that commit and reinstall.

`services/ini_generator.py` turns the configuration's fields into INI-Generator's form rows. INI-Generator applies `settings_default.ini` and the customer profiles, and lists anything it couldn't decide. Those warnings are written as comments at the top of the generated INI.

INI-Generator needs `settings_default.ini` and `customer_profiles.json`. CALIGA downloads them once per run from `gs://<GCS_BUCKET_NAME>/private/ini-generator/`, or reads them from the folder in `INI_GENERATOR_PRIVATE_DIR` when that's set. Restart CALIGA after updating either file.

The LEAF & Wallet fields (Leaf Si/Cc, Keyset ID, Custom Keyset, ECP DESFire, MiFare2Go, ECP TCI, Meridian bit count) and BLE Advertising Name feed INI-Generator directly. Blank fields leave those settings at defaults.

`tools/backfill_ini_fields.py` fills those fields from each configuration's master INI in `input/<version>/`. It only fills blank fields and adds a revision history entry for each configuration it changes. Run it without arguments for a dry run, and with `--apply` to save.

## Flashing

Flashing uses the serial port of the machine running CALIGA, so the reader must be connected to that machine. If the adapter isn't detected automatically, set `OSDP_SERIAL_PORT` in `.env`.

On the Failures dashboard, **Flash Reader** saves the edited INI, forces a fresh Forge build from it, and flashes the result. It stops at the first step that fails.
