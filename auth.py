"""
Who is using CALIGA, and what they're allowed to do.

AUTH_MODE picks how people sign in:
- "dev" (default): anyone can pick any active user from a menu. For local development only.
- Google sign-in, Google IAP, or passwords plug in later. Each one ends by calling
  find_login_user(email) and then login_user(user), so everything else stays the same.

Only users in the users table who are active can sign in. Everyone else is blocked.
Admins (is_admin) can add users and change roles on the Users page.
"""
import os
import logging
from functools import wraps
from flask import g, session, abort
from sqlalchemy import func
from models import db, User
from utils.errors import UserFacingError

log = logging.getLogger(__name__)

ROLES = ('Sales', 'QA', 'SET')
AUTH_MODES = ('dev',)
ALLOWED_EMAIL_DOMAIN = os.getenv('ALLOWED_EMAIL_DOMAIN', 'wavelynx.com').lower()


class LoginBlocked(UserFacingError):
    pass


def auth_mode():
    mode = os.getenv('AUTH_MODE', 'dev').strip().lower()
    if mode not in AUTH_MODES:
        raise RuntimeError(f"AUTH_MODE '{mode}' isn't supported yet. Supported: {', '.join(AUTH_MODES)}.")
    return mode


def is_dev_login():
    return auth_mode() == 'dev'


def find_login_user(email):
    """Returns the active user with this email. Raises LoginBlocked for anyone else."""
    email = (email or '').strip().lower()
    if not email.endswith('@' + ALLOWED_EMAIL_DOMAIN):
        raise LoginBlocked(f"Only @{ALLOWED_EMAIL_DOMAIN} accounts can use CALIGA.")
    user = User.query.filter(func.lower(User.email) == email).first()
    if user is None or not user.is_active:
        raise LoginBlocked(f"{email} doesn't have access to CALIGA. Ask James H. to add you.")
    return user


def login_user(user):
    session.clear()
    session['user_id'] = user.id
    session.permanent = True
    g.pop('current_user', None)
    log.info("%s signed in (%s).", user.email, auth_mode())


def logout_user():
    session.clear()
    g.pop('current_user', None)


def get_current_user():
    """Returns the signed-in user, or None. A user deactivated mid-session is signed out."""
    if 'current_user' not in g:
        user_id = session.get('user_id')
        user = db.session.get(User, user_id) if user_id else None
        g.current_user = user if user and user.is_active else None
    return g.current_user


def require_role(*roles):
    """Rejects the request with 403 unless the current user has one of the given roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if user is None or user.role not in roles:
                abort(403, description=f"This action requires the {' or '.join(roles)} role.")
            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_admin(view):
    """Rejects the request with 403 unless the current user is an admin."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None or not user.is_admin:
            abort(403, description="Only CALIGA admins can manage users.")
        return view(*args, **kwargs)
    return wrapped
