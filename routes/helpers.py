import io
import shutil
from flask import request, abort, jsonify, send_file
from models import db, Configuration, RevisionHistory
from auth import get_current_user


def wants_json():
    return request.accept_mimetypes.best == 'application/json'


def json_error(message, status=400):
    return jsonify({"error": message}), status


def current_email():
    return get_current_user().email


def get_usable_config(config_name):
    """Returns the configuration, or aborts with 404 if missing and 403 if Archived."""
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    if config.status == 'Archived':
        abort(403, description=f"'{config_name}' is Archived. Tools are disabled for Archived configurations.")
    return config


def log_revision(config_name, details, user_email=None):
    db.session.add(RevisionHistory(
        config_name=config_name,
        user_email=user_email or current_email(),
        revision_details=details
    ))


def set_status(config, new_status):
    """Changes the status and records the old one, only when the status actually changes."""
    if config.status == new_status:
        return False
    config.previous_status = config.status
    config.status = new_status
    return True


def send_and_cleanup(path, download_name, cleanup_dir):
    """Reads the file into memory, deletes its temp directory, and sends it as a download."""
    with open(path, 'rb') as f:
        data = io.BytesIO(f.read())
    shutil.rmtree(cleanup_dir, ignore_errors=True)
    return send_file(data, as_attachment=True, download_name=download_name)
