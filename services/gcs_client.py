import os
import logging
import threading
from google.cloud import storage
from utils.errors import UserFacingError

log = logging.getLogger(__name__)

GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID', 'erebus-257721')
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'wavelynx_apex_config')

_client = None
_client_lock = threading.Lock()


def check_gcs_credentials():
    """Fails at startup when GOOGLE_APPLICATION_CREDENTIALS points to a missing file."""
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if creds_path and not os.path.exists(creds_path):
        raise RuntimeError(
            f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file ({creds_path}). "
            "Fix the path, or unset the variable to use your gcloud application-default login."
        )


def get_gcs_client():
    """Returns a shared GCS client, or None when one can't be created."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = storage.Client(project=GCP_PROJECT_ID)
            except Exception as e:
                log.warning("Couldn't initialize GCS client: %s", e)
                return None
        return _client


class GcsWriteRefused(UserFacingError):
    pass


# Everyone's Forge work goes to this one folder: forge/<FORGE_FOLDER_EMAIL>/.
FORGE_FOLDER_EMAIL = os.getenv('FORGE_FOLDER_EMAIL', 'jhepburn@wavelynx.com').strip()


def forge_root():
    """The only GCS folder CALIGA writes to, whoever is signed in."""
    return f"forge/{FORGE_FOLDER_EMAIL}/"


def check_forge_path(path):
    """Raises GcsWriteRefused for any path outside forge_root()."""
    root = forge_root()
    if (not FORGE_FOLDER_EMAIL or "/" in FORGE_FOLDER_EMAIL or ".." in path or "//" in path
            or not path.startswith(root) or path == root):
        raise GcsWriteRefused(f"Refused to write gs://{GCS_BUCKET_NAME}/{path}: CALIGA only writes inside {root}.")
    return path


def write_to_forge(bucket, path, *, text=None, local_path=None):
    """The only way CALIGA writes to GCS. Uploads text or a local file after checking the path."""
    blob = bucket.blob(check_forge_path(path))
    if local_path is not None:
        blob.upload_from_filename(local_path)
    else:
        blob.upload_from_string(text, content_type="text/plain")
    return path
