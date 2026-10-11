import io
from flask import Blueprint, jsonify, send_file, abort
from auth import get_current_user
from jobs import get_job, read_job_file

bp = Blueprint('jobs', __name__)


def _own_job_or_404(job_id):
    job = get_job(job_id, get_current_user().id)
    if job is None:
        abort(404, description="Job not found. It may have expired or belong to another user.")
    return job


@bp.route('/jobs/<job_id>')
def job_status(job_id):
    return jsonify(_own_job_or_404(job_id).to_dict())


@bp.route('/jobs/<job_id>/files/<key>')
def job_file(job_id, key):
    found = read_job_file(_own_job_or_404(job_id), key)
    if found is None:
        abort(404)
    data, download_name = found
    return send_file(io.BytesIO(data), as_attachment=True, download_name=download_name)
