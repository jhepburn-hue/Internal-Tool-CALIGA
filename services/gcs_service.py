import os
from google.cloud import storage

GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID')
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'wavelynx_apex_config')

def get_gcs_client():
    """
    Initializes and returns a Google Cloud Storage client if credentials exist.
    """
    credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if credentials_path and os.path.exists(credentials_path):
        return storage.Client(project=GCP_PROJECT_ID)
    return None

def fetch_artifact_with_fallback(file_name, fw_version, user_email, destination_path):
    """
    Searches for a file in the GCS Input Bucket path first.
    If not found, falls back to searching in the Forge Bucket path.

    Paths:
    - Primary (Input): wavelynx_apex_config/input/{fw_version}/{file_name}
    - Fallback (Forge): wavelynx_apex_config/forge/{user_email}/{fw_version}/{file_name}
    """
    client = get_gcs_client()
    
    primary_blob_path = f"input/{fw_version}/{file_name}"
    fallback_blob_path = f"forge/{user_email}/{fw_version}/{file_name}"

    if not client:
        print(f"[GCS SERVICE] Local credentials missing. Simulating artifact download for {file_name} ({fw_version})")
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, 'w') as f:
            f.write(f"# Simulated GCS artifact for {file_name}\n# FW Version: {fw_version}\n")
        return True

    try:
        bucket = client.bucket(GCS_BUCKET_NAME)

        primary_blob = bucket.blob(primary_blob_path)
        if primary_blob.exists():
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            primary_blob.download_to_filename(destination_path)
            print(f"[GCS SERVICE] Found & downloaded from Input path: {primary_blob_path}")
            return True

        print(f"[GCS SERVICE] File '{file_name}' not found in Input path '{primary_blob_path}'. Searching Forge path '{fallback_blob_path}'...")
        fallback_blob = bucket.blob(fallback_blob_path)
        if fallback_blob.exists():
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            fallback_blob.download_to_filename(destination_path)
            print(f"[GCS SERVICE] Found & downloaded from Forge fallback path: {fallback_blob_path}")
            return True
        else:
            print(f"[GCS SERVICE] Error: '{file_name}' was not found in either Input or Forge bucket paths.")
            return False

    except Exception as e:
        print(f"[GCS SERVICE] Exception during download of {file_name}: {e}")
        return False

def download_ini_file(config_name, fw_version, user_email, output_dir="downloads"):
    """Downloads the .ini configuration file."""
    file_name = f"{config_name}.ini"
    dest_path = os.path.join(output_dir, file_name)
    success = fetch_artifact_with_fallback(file_name, fw_version, user_email, dest_path)
    return dest_path if success else None

def download_profile_bin(config_name, fw_version, user_email, output_dir="downloads"):
    """Downloads the Profile .bin file."""
    file_name = f"{config_name}_profile.bin"
    dest_path = os.path.join(output_dir, file_name)
    success = fetch_artifact_with_fallback(file_name, fw_version, user_email, dest_path)
    return dest_path if success else None

def download_firmware_bin(fw_version, user_email, output_dir="downloads"):
    """Downloads the Firmware .bin file based on firmware version."""
    file_name = f"firmware_{fw_version}.bin"
    dest_path = os.path.join(output_dir, file_name)
    success = fetch_artifact_with_fallback(file_name, fw_version, user_email, dest_path)
    return dest_path if success else None