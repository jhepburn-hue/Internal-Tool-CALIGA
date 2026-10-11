from functools import wraps
from flask import g, session, abort
from models import db, User

ROLES = ('Sales', 'QA', 'SET')


def get_current_user():
    """Returns the User picked with /switch_user, or None."""
    if 'current_user' not in g:
        user_id = session.get('user_id')
        g.current_user = db.session.get(User, user_id) if user_id else None
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
