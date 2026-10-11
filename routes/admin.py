import re
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func
from models import db, User
from auth import ROLES, ALLOWED_EMAIL_DOMAIN, require_admin, get_current_user

log = logging.getLogger(__name__)

bp = Blueprint('admin', __name__)


@bp.route('/admin/users', methods=['GET'])
@require_admin
def users():
    all_users = User.query.order_by(User.is_active.desc(), User.role, User.first_name).all()
    return render_template('admin_users.html', users=all_users, roles=ROLES, email_domain=ALLOWED_EMAIL_DOMAIN)


@bp.route('/admin/users', methods=['POST'])
@require_admin
def add_user():
    first_name = request.form.get('first_name', '').strip()
    last_initial = request.form.get('last_name_initial', '').strip().rstrip('.').upper()
    email = request.form.get('email', '').strip().lower()
    role = request.form.get('role', '')

    error = None
    if not first_name or not re.fullmatch(r'[A-Z]{1,5}', last_initial):
        error = "Enter a first name and a last name initial."
    elif not re.fullmatch(r'[^@\s]+@' + re.escape(ALLOWED_EMAIL_DOMAIN), email):
        error = f"Email must be an @{ALLOWED_EMAIL_DOMAIN} address."
    elif role not in ROLES:
        error = "Pick a role."
    elif User.query.filter(func.lower(User.email) == email).first():
        error = f"{email} is already a user."

    if error:
        flash(error, "danger")
        return redirect(url_for('admin.users'))

    db.session.add(User(first_name=first_name, last_name_initial=last_initial, email=email, role=role))
    db.session.commit()
    log.info("%s added user %s (%s).", get_current_user().email, email, role)
    flash(f"Added {first_name} {last_initial}. ({email}) as {role}.", "success")
    return redirect(url_for('admin.users'))


@bp.route('/admin/users/<int:user_id>', methods=['POST'])
@require_admin
def update_user(user_id):
    user = db.get_or_404(User, user_id)
    admin = get_current_user()
    role = request.form.get('role')
    active = request.form.get('is_active')

    if role is not None:
        if role not in ROLES:
            flash("Pick a valid role.", "danger")
            return redirect(url_for('admin.users'))
        if role != user.role:
            log.info("%s changed %s from %s to %s.", admin.email, user.email, user.role, role)
            flash(f"{user.display_name} is now {role}.", "success")
            user.role = role

    if active is not None:
        make_active = active == 'true'
        if user.id == admin.id and not make_active:
            flash("You can't deactivate your own account.", "danger")
            return redirect(url_for('admin.users'))
        if make_active != user.is_active:
            user.is_active = make_active
            log.info("%s %s %s.", admin.email, "reactivated" if make_active else "deactivated", user.email)
            flash(f"{user.display_name} {'can sign in again' if make_active else 'can no longer sign in'}.", "success")

    db.session.commit()
    return redirect(url_for('admin.users'))
