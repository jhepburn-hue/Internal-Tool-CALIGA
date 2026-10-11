from flask import Blueprint, render_template, session, request, redirect, url_for, flash, abort
from sqlalchemy import func
from models import User
from auth import ROLES

bp = Blueprint('main', __name__)


@bp.route('/')
def home():
    role_descriptions = {
        'SET': [
            'Initialize and manage Firmware execution runs',
            'Execute test groups and validate individual criteria',
            'Claim, remediate, and re-test failures in the Failures dashboard',
            'Inspect and edit INI overrides, then flash readers over OSDP',
            'Review revision history logs and modification tracking',
            'Dispatch automated test requests to QA'
        ],
        'QA': [
            'Initiate firmware execution test cycles',
            'Execute assigned test suites and record validation criteria',
            'Flash readers over OSDP for testing',
            'Analyze firmware test run telemetry and aggregate statistics'
        ],
        'Sales': [
            'Provision new reader configuration profiles',
            'Modify parameter details for draft configurations',
            'Toggle configuration deployment state between POC and Active',
            'Dispatch testing notification alerts to QA',
            'Review revision history and Firmware run statistics'
        ]
    }

    status_descriptions = {
        'Production': 'Validated configuration profile approved for official deployment and manufacturing.',
        'Active': 'Deployed profile requiring verification and testing prior to Production promotion.',
        'POC': 'Proof of Concept profile open for editing and preliminary experimentation.',
        'Archived': 'Deprecated configuration profile retained strictly for historical reference.'
    }

    return render_template(
        'home.html',
        role_descriptions=role_descriptions,
        status_descriptions=status_descriptions
    )


@bp.route('/switch_user/<role>')
def switch_user(role):
    """Utility route to switch the session persona. Optional ?email= picks a specific user in that role."""
    role_match = next((r for r in ROLES if r.lower() == role.lower()), None)
    if role_match is None:
        abort(404)

    query = User.query.filter_by(role=role_match)
    email = request.args.get('email', '').strip().lower()
    if email:
        query = query.filter(func.lower(User.email) == email)

    user = query.order_by(User.id).first()
    if user is None:
        flash(f"No {role_match} user found.", "warning")
        return redirect(url_for('main.home'))

    session['user_id'] = user.id
    flash(f"Now acting as {user.first_name} {user.last_name_initial}. ({user.role}).", "info")

    referrer = request.referrer or ''
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for('main.home'))
