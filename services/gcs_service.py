import os
import time
from google.cloud import storage
from services.ini_generator import generate_ini_from_config
from services.forge_service import trigger_forge_build

GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID', 'erebus-257721')
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'wavelynx_apex_config')

def get_gcs_client():
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if creds_path and not os.path.exists(creds_path):
        del os.environ['GOOGLE_APPLICATION_CREDENTIALS']

    try:
        return storage.Client(project=GCP_PROJECT_ID)
    except Exception as e:
        print(f"[GCS SERVICE] Warning: Couldn't initialize GCS client ({e}).")
        return None

def find_latest_matching_blob(bucket, prefix, filename_targets):
    """
    Searches GCS under `prefix` (including subdirectories like output/v5.4.10/YYYYMMDD_HHMMSS_.../)
    for target filenames and returns the newest blob based on creation time.
    """
    blobs = list(bucket.list_blobs(prefix=prefix))
    matching_blobs = []

    for blob in blobs:
        blob_name = blob.name
        for target in filename_targets:
            if blob_name.endswith(f"/{target}") or blob_name == target:
                matching_blobs.append(blob)

    if not matching_blobs:
        return None

    matching_blobs.sort(key=lambda b: b.time_created, reverse=True)
    return matching_blobs[0]

def get_or_create_ini_file(config, fw_version, user_email, output_dir="downloads"):
    file_name = f"{config.config_name}.ini"
    local_path = os.path.join(output_dir, file_name)
    os.makedirs(output_dir, exist_ok=True)

    clean_ver = fw_version.strip()
    ver_with_v = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    ver_no_v = clean_ver.lstrip('v')

    candidate_prefixes = [
        f"input/{ver_with_v}/",
        f"input/{ver_no_v}/",
        f"forge/{user_email}/{ver_with_v}/",
        f"forge/{user_email}/{ver_no_v}/",
    ]

    client = get_gcs_client()

    if client:
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            for prefix in candidate_prefixes:
                blob = find_latest_matching_blob(bucket, prefix, [file_name])
                if blob:
                    blob.download_to_filename(local_path)
                    print(f"[GCS SERVICE] SUCCESS: Found and downloaded INI -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                    return local_path
        except Exception as e:
            print(f"[GCS SERVICE] Exception checking bucket: {e}")

    print(f"[GCS SERVICE] '{file_name}' not found in GCS. Generating from DB model...")
    generate_ini_from_config(config, local_path)

    if client:
        forge_upload_path = f"forge/{user_email}/{ver_with_v}/{file_name}"
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(forge_upload_path)
            blob.upload_from_filename(local_path)
            print(f"[GCS SERVICE] Uploaded generated INI to Forge Bucket -> gs://{GCS_BUCKET_NAME}/{forge_upload_path}")
        except Exception as e:
            print(f"[GCS SERVICE] Could not upload to Forge bucket: {e}")

    return local_path

def get_or_build_profile_bin(config, fw_version, user_email, output_dir="downloads", timeout_seconds=60):
    """
    Downloads profile .bin file if it exists in GCS output/ (including timestamp folders) or forge/.
    If missing, ensures .ini is in Forge bucket, triggers Forge build,
    and polls GCS for up to 60 seconds until the .bin file lands.
    """
    clean_ver = fw_version.strip()
    ver_with_v = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    ver_no_v = clean_ver.lstrip('v')
    
    config_name = config.config_name
    local_path = os.path.join(output_dir, f"{config_name}_{ver_with_v}.bin")
    os.makedirs(output_dir, exist_ok=True)

    bin_targets = [
        f"{config_name}.bin",
        f"{config_name}_profile.bin",
        f"{config_name}.BIN",
        f"{config_name}_profile.BIN",
    ]

    versions = [ver_with_v, ver_no_v]
    output_prefixes = [f"output/{v}/" for v in versions]
    forge_prefixes = [f"forge/{user_email}/{v}/" for v in versions]

    client = get_gcs_client()

    if client:
        bucket = client.bucket(GCS_BUCKET_NAME)

        for prefix in output_prefixes:
            blob = find_latest_matching_blob(bucket, prefix, bin_targets)
            if blob:
                blob.download_to_filename(local_path)
                print(f"[GCS SERVICE] SUCCESS: Found Profile BIN in Output Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                return local_path

        for prefix in forge_prefixes:
            blob = find_latest_matching_blob(bucket, prefix, bin_targets)
            if blob:
                blob.download_to_filename(local_path)
                print(f"[GCS SERVICE] SUCCESS: Found Profile BIN in Forge Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                return local_path

    ini_path = get_or_create_ini_file(config, ver_with_v, user_email, output_dir)

    trigger_forge_build(config.config_name, ver_with_v, source="user")

    if client:
        bucket = client.bucket(GCS_BUCKET_NAME)
        start_time = time.time()
        print(f"[GCS SERVICE] Polling GCS for {config.config_name} BIN file in Forge (up to {timeout_seconds}s)...")

        while time.time() - start_time < timeout_seconds:
            for prefix in forge_prefixes:
                blob = find_latest_matching_blob(bucket, prefix, bin_targets)
                if blob:
                    blob.download_to_filename(local_path)
                    print(f"[GCS SERVICE] SUCCESS: BIN file landed in Forge Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                    return local_path
            time.sleep(3)

    print(f"[GCS SERVICE] Timed out waiting for {config.config_name} BIN file from Forge.")
    return None

def get_or_build_firmware_bin(config, fw_version, user_email, output_dir="downloads", timeout_seconds=60):
    """
    Downloads firmware DCK binary if it exists in GCS output/ or forge/.
    If missing, ensures .ini is in Forge bucket, triggers Forge build,
    and polls GCS for up to 60 seconds until the file lands.
    """
    clean_ver = fw_version.strip()
    ver_with_v = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    ver_no_v = clean_ver.lstrip('v')
    
    config_name = config.config_name
    local_path = os.path.join(output_dir, f"{config_name}_{ver_with_v}_firmware.dck.bin")
    os.makedirs(output_dir, exist_ok=True)

    dck_targets = [
        f"{config_name}_wall_DCK.bin",
        f"{config_name}_DCK.bin",
        f"{config_name}_wall_dck.bin",
        f"{config_name}_dck.bin",
        f"{config_name}_{ver_with_v}_DCK.bin",
        f"{config_name}_{ver_no_v}_DCK.bin",
    ]

    versions = [ver_with_v, ver_no_v]
    output_prefixes = [f"output/{v}/" for v in versions]
    forge_prefixes = [f"forge/{user_email}/{v}/" for v in versions]

    client = get_gcs_client()

    if client:
        bucket = client.bucket(GCS_BUCKET_NAME)

        for prefix in output_prefixes:
            blob = find_latest_matching_blob(bucket, prefix, dck_targets)
            if blob:
                blob.download_to_filename(local_path)
                print(f"[GCS SERVICE] SUCCESS: Found Firmware BIN in Output Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                return local_path

        for prefix in forge_prefixes:
            blob = find_latest_matching_blob(bucket, prefix, dck_targets)
            if blob:
                blob.download_to_filename(local_path)
                print(f"[GCS SERVICE] SUCCESS: Found Firmware BIN in Forge Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                return local_path

    ini_path = get_or_create_ini_file(config, ver_with_v, user_email, output_dir)

    trigger_forge_build(config.config_name, ver_with_v, source="user", firmware_build_id="422313")

    if client:
        bucket = client.bucket(GCS_BUCKET_NAME)
        start_time = time.time()
        print(f"[GCS SERVICE] Polling GCS for {config.config_name} Firmware BIN in Forge (up to {timeout_seconds}s)...")

        while time.time() - start_time < timeout_seconds:
            for prefix in forge_prefixes:
                blob = find_latest_matching_blob(bucket, prefix, dck_targets)
                if blob:
                    blob.download_to_filename(local_path)
                    print(f"[GCS SERVICE] SUCCESS: Firmware BIN landed in Forge Bucket -> gs://{GCS_BUCKET_NAME}/{blob.name}")
                    return local_path
            time.sleep(3)

    print(f"[GCS SERVICE] Timed out waiting for {config.config_name} Firmware BIN from Forge.")
    return None

def upload_partial_ini_to_gcs(ini_filename, ini_content, fw_version, user_email):
    """
    Uploads a partial .ini file directly to:
    forge/{user_email}/{fw_version}/partials/{ini_filename}
    """
    client = get_gcs_client()
    if not client:
        print("[GCS ERROR] GCS client not available for partial upload.")
        return None

    clean_ver = fw_version.strip()
    ver_with_v = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"

    try:
        bucket = client.bucket(GCS_BUCKET_NAME)
        gcs_path = f"forge/{user_email}/{ver_with_v}/partials/{ini_filename}"
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(ini_content, content_type="text/plain")
        print(f"[GCS SUCCESS] Uploaded partial INI to gs://{GCS_BUCKET_NAME}/{gcs_path}")
        return gcs_path
    except Exception as e:
        print(f"[GCS UPLOAD ERROR] Failed to upload partial INI: {e}")
        return None