from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import db, Configuration, FWRun, ConfigurationTestGroup, TestCase
from auth import require_role
from services.slack_service import send_failure_message
from services.jira_service import create_failure_ticket
from routes.helpers import current_email, log_revision, set_status
from utils.versions import parse_fw_version

bp = Blueprint('testing', __name__)

# Production configs are included so they can be re-validated against new firmware.
TESTABLE_STATUSES = ('Active', 'Production')


def build_test_criteria(config_obj):
    """Returns (category, label, expected_value) tuples for a configuration's test suite."""
    action_categories = [
        {
            "category": "ACTION: Verify AV and Output Formats.",
            "items": [
                ("Idle LED", config_obj.idle_led or "Off"),
                ("Credential Report LED", config_obj.credential_report_led or "Off"),
                ("Beeper", config_obj.beeper or "Off"),
                ("Keypad Format", config_obj.keypad_format or "8-bit"),
                ("Tamper Monitoring", config_obj.tamper_monitoring or "Off"),
                ("Config ID", f"{config_obj.config_id_dec or '0'} ({config_obj.config_id_hex or '0x00'})")
            ]
        },
        {
            "category": "ACTION: Present the HF credential.",
            "items": [
                ("Leaf Si Application (Kv1)", "Leaf Si" if config_obj.card_type in ["Leaf Si", "Dual"] else "None"),
                ("Leaf Cc Application (Kc1)", "Leaf Cc" if config_obj.card_type in ["Leaf Cc", "Dual"] else "None"),
                ("Other Custom HF Application", "None")
            ]
        },
        {
            "category": "ACTION: Verify Card Serial Number (CSN) credentials.",
            "items": [
                ("MFC CSN", config_obj.mfc_csn or "Off"),
                ("EV1/EV2 CSN", config_obj.ev1_ev2_csn or "Off"),
                ("iClass CSN", config_obj.iclass_csn or "Off"),
                ("ISO 15693 CSN", config_obj.iso_15693_csn or "Off"),
                ("ISO 14443A", config_obj.iso_14443a_csn or "Off")
            ]
        },
        {
            "category": "ACTION: Verify Low Frequency (LF) credentials.",
            "items": [
                ("FSK Prox", config_obj.fsk_prox or "Off"),
                ("ASK Prox", config_obj.ask_prox or "Off"),
                ("Prox Filter", config_obj.prox_filter or "Off"),
                ("Prox Filter Description", config_obj.prox_filter_description or "None")
            ]
        },
        {
            "category": "ACTION: Verify Mobile configurations.",
            "items": [
                ("BLE Advertising Config", "Unique (Differs)"),
                ("BLE Functionality", config_obj.ble_functionality or "Admin + Credentials"),
                ("NFC Functionality", config_obj.nfc_functionality or "Enabled"),
                ("MyPass Keyset", config_obj.mobile_keyset or "Standard"),
                ("Legacy Credentials", config_obj.legacy_credentials or "Off"),
                ("Wallet", "Off")
            ]
        }
    ]

    return [(cat["category"], label, value) for cat in action_categories for label, value in cat["items"]]


@bp.route('/testing', methods=['GET'], strict_slashes=False)
def testing_dashboard():
    current_fw = parse_fw_version(request.args.get('fw_version'))

    runs = FWRun.query.filter_by(fw_version=current_fw).order_by(FWRun.run_date.desc()).all()
    selectable_configs = Configuration.query.filter(
        Configuration.status.in_(TESTABLE_STATUSES)
    ).order_by(Configuration.config_name.asc()).all()

    return render_template(
        'testing.html',
        current_fw=current_fw,
        runs=runs,
        selectable_configs=selectable_configs
    )


@bp.route('/testing/start-run', methods=['POST'])
@require_role('QA', 'SET')
def start_new_run():
    fw_version = parse_fw_version(request.form.get('fw_version'))
    selected_config_names = request.form.getlist('config_names')

    configs = Configuration.query.filter(
        Configuration.config_name.in_(selected_config_names),
        Configuration.status.in_(TESTABLE_STATUSES)
    ).all() if selected_config_names else []

    skipped = sorted(set(selected_config_names) - {c.config_name for c in configs})
    if skipped:
        flash(f"Skipped configurations that aren't Active or Production: {', '.join(skipped)}.", "warning")

    if not configs:
        flash("Please select at least one Active or Production configuration to start a firmware run.", "warning")
        return redirect(url_for('testing.testing_dashboard', fw_version=fw_version))

    new_run = FWRun(fw_version=fw_version)
    db.session.add(new_run)

    for config_obj in configs:
        test_group = ConfigurationTestGroup(
            fw_run=new_run,
            configuration_id=config_obj.id,
            config_name=config_obj.config_name,
            status='Untested'
        )
        db.session.add(test_group)

        for category, label, expected in build_test_criteria(config_obj):
            db.session.add(TestCase(
                test_group=test_group,
                category=category,
                criterion_name=label,
                expected_value=expected,
                status='Untested'
            ))

    db.session.commit()
    flash(f"Started new FW {fw_version} run with {len(configs)} test suite(s).", "success")
    return redirect(url_for('testing.testing_dashboard', fw_version=fw_version))


@bp.route('/testing/assign-group/<int:group_id>', methods=['POST'])
@require_role('QA', 'SET')
def assign_test_group(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    action = request.form.get('action', 'assign')

    if action == 'assign':
        group.assigned_user = user_email
        flash(f"Assigned test group '{group.config_name}' to {user_email}.", "info")
    else:
        group.assigned_user = None
        flash(f"Unassigned test group '{group.config_name}'.", "info")

    db.session.commit()
    return redirect(url_for('testing.testing_dashboard', fw_version=group.fw_run.fw_version))


@bp.route('/testing/submit-group/<int:group_id>', methods=['POST'])
@require_role('QA', 'SET')
def submit_test_group(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_run = group.fw_run
    back = redirect(url_for('testing.testing_dashboard', fw_version=fw_run.fw_version))

    if group.status in ('Passed', 'Failed'):
        flash(f"Test group '{group.config_name}' was already submitted as {group.status}.", "warning")
        return back

    if not group.assigned_user:
        flash("Cannot submit test group until a tester is assigned.", "danger")
        return back

    if group.assigned_user != user_email:
        flash(f"Only the assigned tester ({group.assigned_user}) can submit this test group.", "danger")
        return back

    has_untested = False
    failed_cases = []

    for tc in group.test_cases:
        status = request.form.get(f"status_{tc.id}")
        comment = request.form.get(f"comment_{tc.id}", "").strip()

        if status == "Failed" and not comment:
            db.session.rollback()
            flash(f"Failure comments are required for failing criteria: '{tc.display_label}'", "danger")
            return back

        if status in ('Untested', 'Passed', 'Failed'):
            tc.status = status
            tc.comment = comment

        if tc.status == 'Untested':
            has_untested = True
        elif tc.status == 'Failed':
            failed_cases.append(tc)

    if has_untested:
        db.session.rollback()
        flash("All criteria must be evaluated (Passed or Failed) before submitting.", "warning")
        return back

    config_obj = group.configuration or Configuration.query.filter_by(config_name=group.config_name).first()

    if failed_cases:
        group.status = 'Failed'

        if config_obj and config_obj.status == 'Production':
            demote_to = config_obj.previous_status if config_obj.previous_status in ('Active', 'POC') else 'Active'
            set_status(config_obj, demote_to)
            log_revision(config_obj.config_name,
                         f"Demoted from Production to {demote_to} after failing FW {fw_run.fw_version} run #{fw_run.id}.",
                         user_email)

        crit_details = ", ".join(
            f"{c.display_label} (expected {c.display_expected}): {c.comment}" for c in failed_cases
        )

        created_key = create_failure_ticket(group.config_name, user_email, fw_run.fw_version, crit_details)
        group.jira_key = created_key

        send_failure_message(group.config_name, user_email, fw_run.fw_version, crit_details)
        if created_key:
            flash(f"Test group '{group.config_name}' marked as FAILED. Jira ticket {created_key} created.", "danger")
        else:
            flash(f"Test group '{group.config_name}' marked as FAILED, but the Jira ticket could not be created. "
                  "Check the Jira settings and the server log.", "warning")

    else:
        group.status = 'Passed'

        if config_obj and config_obj.status == 'Archived':
            flash(f"Test group '{group.config_name}' PASSED. The configuration is Archived, so it stays Archived.", "info")
        elif config_obj:
            old_status = config_obj.status
            if set_status(config_obj, 'Production'):
                log_revision(config_obj.config_name,
                             f"Promoted from {old_status} to Production after passing FW {fw_run.fw_version} run #{fw_run.id}.",
                             user_email)
            flash(f"Test group '{group.config_name}' PASSED! Configuration is in Production.", "success")

    db.session.commit()
    return back
