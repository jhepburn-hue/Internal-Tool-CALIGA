import os
import secrets
import string
import requests
from urllib.parse import quote
from services.gcs_service import get_or_build_profile_bin, get_or_build_firmware_bin

PB_BASE_URL = os.getenv('PB_BASE_URL', 'https://unf.wavelynxtech.com').rstrip('/')
PB_COLLECTION_NAME = os.getenv('PB_COLLECTION_ID', 'cards')
PB_ADMIN_TOKEN = os.getenv('PB_ADMIN_TOKEN')

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

def sync_token_record(token_name, file_path):
    """
    Searches PocketBase on unf.wavelynxtech.com for matching name.
    - If found: Updates the record and returns existing token.
    - If NOT found: Creates new record and returns generated token.
    """
    if not os.path.exists(file_path):
        print(f"[POCKETBASE] Error: File path '{file_path}' does not exist.")
        return None

    clean_name = token_name.strip()
    headers = get_headers()
    filename = os.path.basename(file_path)

    collections_to_try = list(dict.fromkeys(['cards', 'kceqp1an569q6sp', PB_COLLECTION_NAME]))

    found_record = None
    active_collection = 'cards'

    for collection in collections_to_try:
        raw_filter = f"name='{clean_name}'"
        search_url = f"{PB_BASE_URL}/api/collections/{collection}/records?filter=({quote(raw_filter)})"

        try:
            resp = requests.get(search_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get('items', [])
                if items:
                    found_record = items[0]
                    active_collection = collection
                    print(f"[POCKETBASE] SUCCESS: Found record in '{collection}' (ID: {found_record['id']}, Token: {found_record.get('token')})")
                    break
            else:
                print(f"[POCKETBASE] Search on '{collection}' status {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[POCKETBASE] Exception searching '{collection}': {e}")

    if found_record:
        record_id = found_record['id']
        existing_token = found_record.get('token')
        update_url = f"{PB_BASE_URL}/api/collections/{active_collection}/records/{record_id}"

        try:
            with open(file_path, 'rb') as f:
                files = {'card': (filename, f, 'application/octet-stream')}
                data = {'status': 'ACTIVE'}
                
                print(f"[POCKETBASE] Updating record '{record_id}' on unf.wavelynxtech.com...")
                res = requests.patch(update_url, headers=headers, data=data, files=files, timeout=15)
                
                if res.status_code == 200:
                    print(f"[POCKETBASE] SUCCESS: Updated PocketBase record '{record_id}'.")
                    return existing_token
                else:
                    print(f"[POCKETBASE] Warning: PATCH status {res.status_code}. Returning token '{existing_token}'.")
                    return existing_token
        except Exception as e:
            print(f"[POCKETBASE] Exception updating record '{record_id}': {e}")
            return existing_token

    print(f"[POCKETBASE] Record '{clean_name}' not found. Creating new entry...")
    new_token = generate_token_string()
    
    for collection in collections_to_try:
        create_url = f"{PB_BASE_URL}/api/collections/{collection}/records"
        try:
            with open(file_path, 'rb') as f:
                files = {'card': (filename, f, 'application/octet-stream')}
                data = {
                    'token': new_token,
                    'name': clean_name,
                    'status': 'ACTIVE'
                }
                res = requests.post(create_url, headers=headers, data=data, files=files, timeout=15)
                if res.status_code in [200, 201]:
                    created_data = res.json()
                    created_token = created_data.get('token', new_token)
                    print(f"[POCKETBASE] SUCCESS: Created new record in '{collection}' with token '{created_token}'.")
                    return created_token
                else:
                    print(f"[POCKETBASE] Error creating record in '{collection}' ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"[POCKETBASE] Exception creating record: {e}")

    return None

def process_get_tokens(config, fw_version, user_email):
    """
    1. Gets/builds Profile BIN and Firmware BIN files.
    2. Builds exact token names ('5.4.10 CQU1 PROFILE' / '5.4.10 CQU1 FIRMWARE').
    3. Syncs both files and returns official tokens from PocketBase.
    """
    clean_ver = fw_version.strip().lstrip('v')
    config_name = config.config_name

    profile_name = f"{clean_ver} {config_name} PROFILE"
    firmware_name = f"{clean_ver} {config_name} FIRMWARE"

    print(f"[POCKETBASE] Retrieving/Building Profile BIN for {profile_name}...")
    profile_bin_path = get_or_build_profile_bin(config, fw_version, user_email)

    print(f"[POCKETBASE] Retrieving/Building Firmware BIN for {firmware_name}...")
    firmware_bin_path = get_or_build_firmware_bin(config, fw_version, user_email)

    profile_token = None
    firmware_token = None

    if profile_bin_path:
        profile_token = sync_token_record(profile_name, profile_bin_path)

    if firmware_bin_path:
        firmware_token = sync_token_record(firmware_name, firmware_bin_path)

    return {
        "profile_name": profile_name,
        "profile_token": profile_token or "Sync Failed",
        "firmware_name": firmware_name,
        "firmware_token": firmware_token or "Sync Failed"
    }