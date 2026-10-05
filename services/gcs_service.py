import os
from google.cloud import storage
from services.ini_generator import generate_ini_from_config

GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID', 'erebus-257721')
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'wavelynx_apex_config')

def get_gcs_client():
    """Initializes GCS client using Application Default Credentials (ADC) or explicit credentials if file exists."""
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    
    if creds_path and not os.path.exists(creds_path):
        del os.environ['GOOGLE_APPLICATION_CREDENTIALS']

    try:
        return storage.Client(project=GCP_PROJECT_ID)
    except Exception as e:
        print(f"[GCS SERVICE] Warning: Couldn't initialize GCS client ({e}). Running in fallback mode.")
        return None

def get_or_create_ini_file(config, fw_version, user_email, output_dir="downloads"):
    """
    1. Searches Input Bucket: input/{fw_version}/{config_name}.ini
    2. Searches Fallback Input Bucket (without 'v' prefix if needed)
    3. Searches Forge Bucket: forge/{user_email}/{fw_version}/{config_name}.ini
    4. If NOT found anywhere, generates INI from DB and uploads to Forge Bucket.
    """
    file_name = f"{config.config_name}.ini"
    local_path = os.path.join(output_dir, file_name)
    os.makedirs(output_dir, exist_ok=True)

    clean_ver = fw_version.strip()
    ver_with_v = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    ver_no_v = clean_ver.lstrip('v')

    candidate_paths = [
        f"input/{ver_with_v}/{file_name}",
        f"input/{ver_no_v}/{file_name}",
        f"forge/{user_email}/{ver_with_v}/{file_name}",
        f"forge/{user_email}/{ver_no_v}/{file_name}",
    ]

    client = get_gcs_client()

    if client:
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            for path in candidate_paths:
                blob = bucket.blob(path)
                if blob.exists():
                    blob.download_to_filename(local_path)
                    print(f"[GCS SERVICE] SUCCESS: Found and downloaded from GCS -> gs://{GCS_BUCKET_NAME}/{path}")
                    return local_path
                else:
                    print(f"[GCS SERVICE] Checked path (not found): gs://{GCS_BUCKET_NAME}/{path}")
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