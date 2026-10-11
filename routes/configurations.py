from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from models import db, Configuration, RevisionHistory
from auth import require_role
from services.slack_service import send_alert_qa_notification
from routes.helpers import current_email, log_revision, set_status
from utils.versions import parse_fw_version

bp = Blueprint('configurations', __name__)

EDITABLE_FIELDS = [
    'idle_led', 'credential_report_led', 'beeper', 'keypad_format',
    'tamper_monitoring', 'casi_output_format', 'supervision_state',
    'card_type', 'facility_code', 'starting_badge', 'bitstream',
    'fsk_prox', 'ask_prox', 'prox_filter', 'prox_filter_description',
    'ble_advertising_config', 'ble_functionality', 'nfc_functionality',
    'mobile_keyset', 'legacy_credentials', 'transport_mode',
    'mfc_csn', 'ev1_ev2_csn', 'iclass_csn', 'iso_15693_csn', 'iso_14443a_csn'
]


def _normalize(val):
    """Normalizes None, 'None', empty strings, and whitespace for clean diffing."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ["none", "null", "-"]:
        return ""
    return s


def _parse_field(field, raw):
    """Returns (value, error) for one submitted form field."""
    raw = (raw or "").strip()
    if field == 'facility_code':
        if raw == "":
            return None, None
        if not raw.isdigit():
            return None, f"Facility Code must be a whole number (got '{raw}')."
        return int(raw), None
    return (raw if _normalize(raw) != "" else None), None


# Arbitrary constant identifying the config ID allocation lock.
CONFIG_ID_LOCK_KEY = 4815162342


def _lock_config_ids():
    """Takes a Postgres transaction lock so only one request allocates a config ID at a time."""
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CONFIG_ID_LOCK_KEY})


def _next_config_id():
    return (db.session.query(func.max(Configuration.config_id_dec)).scalar() or 0) + 1


@bp.route('/configurations')
def configurations_list():
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = Configuration.query

    if search_query:
        query = query.filter(
            (Configuration.config_name.ilike(f'%{search_query}%')) |
            (Configuration.config_id_hex.ilike(f'%{search_query}%')) |
            (db.cast(Configuration.config_id_dec, db.String).ilike(f'%{search_query}%'))
        )

    if status_filter:
        query = query.filter(Configuration.status == status_filter)

    configurations = query.order_by(Configuration.config_id_dec.asc().nulls_last()).all()

    return render_template(
        'configurations.html',
        configurations=configurations,
        search_query=search_query,
        status_filter=status_filter
    )


@bp.route('/configurations/<config_name>')
def configuration_details(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    revisions = RevisionHistory.query.filter_by(config_name=config_name).order_by(RevisionHistory.revision_date.desc()).all()

    return render_template('configuration_details.html', config=config, revisions=revisions)


@bp.route('/configurations/toggle_status/<config_name>', methods=['POST'])
@require_role('Sales')
def toggle_status(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()

    transitions = {'POC': 'Active', 'Active': 'POC'}
    new_status = transitions.get(config.status)
    if new_status is None:
        flash("Only POC and Active configurations can be toggled.", "warning")
        return redirect(url_for('configurations.configuration_details', config_name=config_name))

    old_status = config.status
    set_status(config, new_status)
    log_revision(config.config_name, f"Status changed from {old_status} to {new_status}.")
    db.session.commit()

    flash(f"Configuration status changed to {new_status}.", "success")
    return redirect(url_for('configurations.configuration_details', config_name=config_name))


@bp.route('/configurations/<config_name>/alert-qa', methods=['POST'])
@require_role('Sales', 'SET')
def alert_qa(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()

    if config.status != 'Active':
        flash("QA can only be alerted for Active configurations.", "warning")
        return redirect(url_for('configurations.configuration_details', config_name=config_name))

    fw_version = parse_fw_version(request.form.get('fw_version'))

    if send_alert_qa_notification(config.config_name, config.status, current_email(), fw_version=fw_version):
        flash(f"QA team alerted for {config_name} via Slack!", "success")
    else:
        flash("Slack alert could not be sent. Check SLACK_WEBHOOK_URL and the server log.", "danger")

    return redirect(url_for('configurations.configuration_details', config_name=config_name))


@bp.route('/configurations/<config_name>/update', methods=['POST'])
@require_role('Sales')
def update_configuration(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()

    if config.status != 'POC':
        flash("Only POC configurations can be edited.", "warning")
        return redirect(url_for('configurations.configuration_details', config_name=config_name))

    changes = []
    for field in EDITABLE_FIELDS:
        if field not in request.form:
            continue

        new_val, error = _parse_field(field, request.form.get(field))
        if error:
            db.session.rollback()
            flash(f"{error} No changes were saved.", "danger")
            return redirect(url_for('configurations.configuration_details', config_name=config_name))

        old_raw = getattr(config, field)
        if _normalize(old_raw) != _normalize(new_val):
            setattr(config, field, new_val)
            disp_old = old_raw if old_raw is not None else "None"
            disp_new = new_val if new_val is not None else "None"
            changes.append(f"{field.replace('_', ' ').title()}: '{disp_old}' → '{disp_new}'")

    if changes:
        log_revision(config.config_name, f"Updated configuration details: {', '.join(changes)}")
        db.session.commit()
        flash("Configuration updated successfully!", "success")
    else:
        flash("No changes detected.", "info")

    return redirect(url_for('configurations.configuration_details', config_name=config_name))


@bp.route('/configurations/new', methods=['GET'])
@require_role('Sales')
def new_configuration():
    next_id_dec = _next_config_id()

    # Preview only. The final ID is allocated when the form is submitted.
    config = Configuration(
        config_name=f"CONFIG_{next_id_dec}",
        config_id_dec=next_id_dec,
        config_id_hex=f"0x{next_id_dec:04X}",
        status="POC"
    )

    return render_template('create_configuration.html', config=config, is_new=True)


@bp.route('/configurations/create', methods=['POST'])
@require_role('Sales')
def create_configuration():
    config_name = request.form.get('config_name', '').strip()
    if not config_name:
        flash("Configuration name is required.", "danger")
        return redirect(url_for('configurations.new_configuration'))

    if Configuration.query.filter_by(config_name=config_name).first():
        flash(f"A configuration with name '{config_name}' already exists.", "danger")
        return redirect(url_for('configurations.new_configuration'))

    values = {}
    for field in EDITABLE_FIELDS:
        value, error = _parse_field(field, request.form.get(field))
        if error:
            flash(error, "danger")
            return redirect(url_for('configurations.new_configuration'))
        values[field] = value

    # Holding the lock until commit stops two configurations created at the
    # same moment from both getting the same "max + 1" ID.
    _lock_config_ids()
    next_id_dec = _next_config_id()
    next_id_hex = f"0x{next_id_dec:04X}"

    db.session.add(Configuration(
        config_name=config_name,
        config_id_dec=next_id_dec,
        config_id_hex=next_id_hex,
        status="POC",
        **values
    ))
    log_revision(config_name, f"Created new configuration (ID Dec: {next_id_dec}, Hex: {next_id_hex}) in POC status.")

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(f"A configuration with name '{config_name}' already exists.", "danger")
        return redirect(url_for('configurations.new_configuration'))

    flash(f"Configuration '{config_name}' successfully created!", "success")
    return redirect(url_for('configurations.configuration_details', config_name=config_name))


@bp.route('/configurations/archive/<config_name>', methods=['POST'])
@require_role('Sales')
def archive_configuration(config_name):
    config_obj = Configuration.query.filter_by(config_name=config_name).first_or_404()

    if config_obj.status == 'Archived':
        flash(f"Configuration '{config_name}' is already archived.", "info")
        return redirect(url_for('configurations.configuration_details', config_name=config_name))

    old_status = config_obj.status
    set_status(config_obj, 'Archived')
    log_revision(config_name, f"Configuration status changed from '{old_status}' to 'Archived'.")
    db.session.commit()

    flash(f"Configuration '{config_name}' archived successfully.", "success")
    return redirect(url_for('configurations.configuration_details', config_name=config_name))
