import os
import logging
import threading
from google.cloud import storage

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
