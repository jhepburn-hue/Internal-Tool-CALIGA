import os
import secrets
import string
import logging
import requests
from urllib.parse import quote
from services.gcs_service import get_or_build_profile_bin, get_or_build_firmware_bin
from utils.errors import UserFacingError
from utils.versions import normalize_fw_version

log = logging.getLogger(__name__)

PB_BASE_URL = os.getenv('PB_BASE_URL', 'https://unf.wavelynxtech.com').rstrip('/')
PB_COLLECTION = os.getenv('PB_COLLECTION_ID', 'cards')
PB_ADMIN_TOKEN = os.getenv('PB_ADMIN_TOKEN')


class PocketBaseError(Exception):
    pass


def _http_error(action, res):
    """Builds a readable PocketBaseError, calling out a rejected admin token."""
    if res.status_code in (401, 403):
        return PocketBaseError(
            f"{action} failed: PocketBase rejected the admin token ({res.status_code}). "
            "It has probably expired. Update PB_ADMIN_TOKEN in .env and restart CALIGA."
        )
    return PocketBaseError(f"{action} failed ({res.status_code}): {res.text[:200]}")


def generate_token_string():
    """Generates a random token string matching PocketBase format (e.g. c-OW3u5Qdp)."""
    alphabet = string.ascii_letters + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(8))
    return f"c-{suffix}"


def get_headers():
    headers = {}
    if PB_ADMIN_TOKEN:
        token = PB_ADMIN_TOKEN.replace('Bearer ', '').strip()
        headers["Authorization"] = f"Bearer {token}"
    return headers


def token_names(config, fw_version):
    """Returns the exact PocketBase record names, e.g. ('5.4.10 CQU1 PROFILE', '5.4.10 CQU1 FIRMWARE')."""
    _, ver_no_v = normalize_fw_version(fw_version)
    return f"{ver_no_v} {config.config_name} PROFILE", f"{ver_no_v} {config.config_name} FIRMWARE"


def _records_url(record_id=None):
    base = f"{PB_BASE_URL}/api/collections/{PB_COLLECTION}/records"
    return f"{base}/{record_id}" if record_id else base


def find_record(token_name):
    """Returns the PocketBase record with this exact name, or None. Raises PocketBaseError on HTTP errors."""
    raw_filter = f"name='{token_name.strip()}'"
    search_url = f"{_records_url()}?filter=({quote(raw_filter)})"

    try:
        resp = requests.get(search_url, headers=get_headers(), timeout=10)
    except requests.RequestException as e:
        raise PocketBaseError(f"Couldn't reach PocketBase: {e}") from e

    if resp.status_code != 200:
        raise _http_error("Search", resp)

    items = resp.json().get('items', [])
    return items[0] if items else None


def sync_token_record(token_name, file_path):
    """
    Uploads file_path to the PocketBase record named token_name.
    - If found: updates the record and returns its existing token.
    - If not found: creates a new record and returns the generated token.
    Raises PocketBaseError when any step fails.
    """
    if not os.path.exists(file_path):
        raise PocketBaseError(f"Binary file '{file_path}' does not exist.")

    clean_name = token_name.strip()
    filename = os.path.basename(file_path)
    record = find_record(clean_name)

    with open(file_path, 'rb') as f:
        files = {'card': (filename, f, 'application/octet-stream')}

        if record:
            log.info("Updating PocketBase record %s (%s)...", record['id'], clean_name)
            try:
                res = requests.patch(_records_url(record['id']), headers=get_headers(),
                                     data={'status': 'ACTIVE'}, files=files, timeout=15)
            except requests.RequestException as e:
                raise PocketBaseError(f"Couldn't reach PocketBase: {e}") from e

            if res.status_code != 200:
                raise _http_error(f"Update of '{clean_name}'", res)
            return record.get('token')

        log.info("Record '%s' not found. Creating new entry...", clean_name)
        data = {'token': generate_token_string(), 'name': clean_name, 'status': 'ACTIVE'}
        try:
            res = requests.post(_records_url(), headers=get_headers(), data=data, files=files, timeout=15)
        except requests.RequestException as e:
            raise PocketBaseError(f"Couldn't reach PocketBase: {e}") from e

        if res.status_code not in [200, 201]:
            raise _http_error(f"Create of '{clean_name}'", res)
        return res.json().get('token', data['token'])


def _entry(name, token=None, error=None):
    return {"name": name, "token": token, "error": error}


def lookup_tokens(config, fw_version):
    """Read-only: returns the existing profile and firmware tokens without building or uploading anything."""
    results = {}
    for kind, name in zip(("profile", "firmware"), token_names(config, fw_version)):
        try:
            record = find_record(name)
            results[kind] = _entry(name, token=record.get('token') if record else None,
                                   error=None if record else "No PocketBase record yet. Use Sync to create one.")
        except PocketBaseError as e:
            results[kind] = _entry(name, error=str(e))
    return results


def sync_tokens(config, fw_version, user_email, output_dir):
    """
    1. Gets/builds Profile BIN and Firmware BIN files.
    2. Uploads both to PocketBase, creating records when missing.
    3. Returns the token, or the error, for each.
    """
    profile_name, firmware_name = token_names(config, fw_version)
    builders = {
        "profile": (profile_name, get_or_build_profile_bin),
        "firmware": (firmware_name, get_or_build_firmware_bin),
    }

    results = {}
    for kind, (name, build) in builders.items():
        log.info("Retrieving/building %s...", name)
        try:
            bin_path = build(config, fw_version, user_email, output_dir)
        except UserFacingError as e:
            results[kind] = _entry(name, error=str(e))
            continue
        if not bin_path:
            results[kind] = _entry(name, error="Binary could not be found or built.")
            continue
        try:
            results[kind] = _entry(name, token=sync_token_record(name, bin_path))
        except PocketBaseError as e:
            log.error("PocketBase sync failed for %s: %s", name, e)
            results[kind] = _entry(name, error=str(e))
    return results
