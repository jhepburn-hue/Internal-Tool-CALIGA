"""
Storage for files produced by background jobs (built binaries, generated INIs).

Files go to GCS (gs://<GCS_BUCKET_NAME>/<JOB_GCS_PREFIX><job id>/<name>) so any server process
can serve them. Set JOB_STORAGE_DIR to keep them in a local folder instead, e.g. for tests.
"""
import os
import shutil
import logging
from services.gcs_client import get_gcs_client, GCS_BUCKET_NAME
from utils.errors import UserFacingError

log = logging.getLogger(__name__)

GCS_PREFIX = os.getenv('JOB_GCS_PREFIX', 'caliga-jobs/')


class JobStorageError(UserFacingError):
    pass


class GcsJobStorage:
    def _bucket(self):
        client = get_gcs_client()
        if client is None:
            raise JobStorageError("Google Cloud Storage is unavailable, so job files can't be stored.")
        return client.bucket(GCS_BUCKET_NAME)

    def save(self, job_id, local_path, name):
        path = f"{GCS_PREFIX}{job_id}/{name}"
        self._bucket().blob(path).upload_from_filename(local_path)
        return path

    def read(self, path):
        return self._bucket().blob(path).download_as_bytes()

    def delete_job(self, job_id):
        bucket = self._bucket()
        for blob in bucket.list_blobs(prefix=f"{GCS_PREFIX}{job_id}/"):
            blob.delete()


class LocalJobStorage:
    def __init__(self, root):
        self.root = root

    def save(self, job_id, local_path, name):
        folder = os.path.join(self.root, job_id)
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(local_path, os.path.join(folder, name))
        return f"{job_id}/{name}"

    def read(self, path):
        with open(os.path.join(self.root, path), 'rb') as f:
            return f.read()

    def delete_job(self, job_id):
        shutil.rmtree(os.path.join(self.root, job_id), ignore_errors=True)


def get_job_storage():
    local_dir = os.getenv('JOB_STORAGE_DIR')
    return LocalJobStorage(local_dir) if local_dir else GcsJobStorage()
