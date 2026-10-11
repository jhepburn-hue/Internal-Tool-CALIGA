from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import db, Configuration, ConfigurationTestGroup
from auth import require_role, get_current_user
from jobs import enqueue, job_handler, JobError
from services.slack_service import send_alert_qa_notification
from services.gcs_service import get_failure_ini_content, save_edited_failure_ini, get_or_build_firmware_bin
from services.jira_service import assign_jira_ticket, transition_jira_issue_to_complete, jira_browse_url
from routes.helpers import current_email, log_revision, json_error, get_usable_config

bp = Blueprint('failures', __name__)


@bp.route('/failures', methods=['GET'], strict_slashes=False)
@require_role('SET')
def failures_dashboard():
    failed_groups = ConfigurationTestGroup.query.filter_by(status='Failed').order_by(ConfigurationTestGroup.id.desc()).all()

    failures_data = []
    for group in failed_groups:
        failed_cases = [tc for tc in group.test_cases if tc.status == 'Failed']

        failures_data.append({
            'group_id': group.id,
            'config_name': group.config_name,
            'fw_version': group.fw_run.fw_version,
            'run_date': group.fw_run.run_date,
            'tester_user': group.assigned_user,
            'claimed_user': group.claimed_user,
            'failed_cases': failed_cases,
            'failure_count': len(failed_cases),
            'jira_key': group.jira_key,
            'jira_url': jira_browse_url(group.jira_key),
        })

    return render_template(
        'failures.html',
        failures=failures_data,
        total_failures=len(failures_data)
    )


@bp.route('/failures/ini/<int:group_id>', methods=['GET'])
@require_role('SET')
def failure_ini(group_id):
    """Loads a failure's INI on demand when its drawer is opened."""
    group = ConfigurationTestGroup.query.get_or_404(group_id)
    content = get_failure_ini_content(group.config_name, group.fw_run.fw_version, current_email())
    return jsonify({"content": content})


@bp.route('/failures/retest/<int:group_id>', methods=['POST'])
@require_role('SET')
def retest_failure(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_ver = group.fw_run.fw_version

    group.status = 'Untested'
    group.assigned_user = None
    group.claimed_user = None

    for tc in group.test_cases:
        tc.status = 'Untested'
        tc.comment = None

    if Configuration.query.filter_by(config_name=group.config_name).first():
        log_revision(group.config_name, f"Re-test requested for FW {fw_ver} run #{group.fw_run_id}. Test suite reset to Untested.")

    db.session.commit()

    if group.jira_key:
        if transition_jira_issue_to_complete(group.jira_key):
            jira_note = f"Jira ticket {group.jira_key} moved to 'Complete'."
        else:
            jira_note = f"Jira ticket {group.jira_key} could not be moved to 'Complete'."
    else:
        jira_note = "No Jira ticket is linked, so Jira was not updated."

    slack_sent = send_alert_qa_notification(group.config_name, "Re-Testing Requested", user_email, fw_ver)
    slack_note = "Slack alert dispatched." if slack_sent else "Slack alert could not be sent."

    flash(f"Reset test suite for '{group.config_name}'. {jira_note} {slack_note}", "success" if group.jira_key and slack_sent else "warning")
    return redirect(url_for('failures.failures_dashboard'))


@bp.route('/failures/save-ini/<int:group_id>', methods=['POST'])
@require_role('SET')
def save_failure_ini(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_ver = group.fw_run.fw_version

    edited_ini = request.form.get('ini_content', '').strip()

    if not edited_ini:
        flash("INI content cannot be empty.", "warning")
        return redirect(url_for('failures.failures_dashboard'))

    if save_edited_failure_ini(group.config_name, edited_ini, fw_ver, user_email):
        flash(f"Updated INI for '{group.config_name}' saved to Forge bucket (forge/{user_email}/{fw_ver}/)!", "success")
    else:
        flash("Failed to save edited INI to Cloud Storage.", "danger")

    return redirect(url_for('failures.failures_dashboard'))


@job_handler('failure_firmware')
def _failure_firmware_job(job, group_id, ini_content, user_email):
    """Saves the edited INI, forces a Forge build from it, and stores the firmware for the browser to flash."""
    group = db.session.get(ConfigurationTestGroup, group_id)
    config = group.configuration or Configuration.query.filter_by(config_name=group.config_name).first()
    if config is None:
        raise JobError(f"Configuration '{group.config_name}' no longer exists.")
    fw_ver = group.fw_run.fw_version

    job.message = "Saving edited INI to Cloud Storage..."
    if not save_edited_failure_ini(config.config_name, ini_content, fw_ver, user_email):
        raise JobError("Couldn't save the edited INI to Cloud Storage. Nothing was built or flashed.")

    job.message = "Building firmware from the edited INI with Forge (up to 60s)..."
    firmware = get_or_build_firmware_bin(config, fw_ver, user_email, job.workdir, force_rebuild=True)
    if not firmware:
        raise JobError(f"Forge didn't produce firmware for {config.config_name} ({fw_ver}) within 60s. Nothing was flashed.")

    job.add_file('bin', firmware, f"{config.config_name}_{fw_ver}_firmware.bin")
    job.result = {"config_name": config.config_name, "fw_version": fw_ver}
    job.message = "Firmware built from the edited INI."


@bp.route('/failures/flash_and_run/<int:group_id>', methods=['POST'])
@require_role('SET')
def flash_and_run(group_id):
    """Saves the edited INI and rebuilds firmware from it. The browser then flashes the result."""
    group = ConfigurationTestGroup.query.get_or_404(group_id)
    get_usable_config(group.config_name)

    ini_content = request.form.get('ini_content', '').strip()
    if not ini_content:
        return json_error("INI content cannot be empty.")

    job_id = enqueue('failure_firmware', get_current_user().id,
                     group_id=group_id, ini_content=ini_content, user_email=current_email())
    return jsonify({"job_id": job_id}), 202


@bp.route('/failures/claim/<int:group_id>', methods=['POST'])
@require_role('SET')
def claim_failure(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    group.claimed_user = user_email
    db.session.commit()

    if not group.jira_key:
        flash(f"Claimed failure for '{group.config_name}'. No Jira ticket is linked, so Jira was not updated.", "warning")
    elif assign_jira_ticket(issue_key=group.jira_key, assignee_email=user_email):
        flash(f"Claimed failure for '{group.config_name}' and assigned Jira ticket {group.jira_key} to {user_email}.", "success")
    else:
        flash(f"Claimed failure locally for '{group.config_name}', but could not assign Jira ticket {group.jira_key}.", "warning")

    return redirect(url_for('failures.failures_dashboard'))


@bp.route('/failures/unclaim/<int:group_id>', methods=['POST'])
@require_role('SET')
def unclaim_failure(group_id):
    user_email = current_email()

    group = ConfigurationTestGroup.query.get_or_404(group_id)

    if group.claimed_user != user_email:
        flash("You can only unclaim failures that you currently own.", "warning")
        return redirect(url_for('failures.failures_dashboard'))

    group.claimed_user = None
    db.session.commit()

    if not group.jira_key:
        flash(f"Unclaimed failure for '{group.config_name}'. No Jira ticket is linked.", "info")
    elif assign_jira_ticket(issue_key=group.jira_key, assignee_email=None):
        flash(f"Unclaimed failure for '{group.config_name}' and unassigned Jira ticket {group.jira_key}.", "info")
    else:
        flash(f"Unclaimed failure for '{group.config_name}', but could not unassign Jira ticket {group.jira_key}.", "warning")

    return redirect(url_for('failures.failures_dashboard'))
