import os
import requests
import time
from datetime import datetime, timezone
from pathlib import Path

FORGE_BASE_URL = os.getenv('FORGE_BASE_URL', 'https://forge.wavelynxdev.com')
ACTIVE_IAP_COOKIE = os.getenv('ACTIVE_IAP_COOKIE', '')
ACTIVE_IAP_UID = os.getenv('ACTIVE_IAP_UID', '112564004525954034965')

FIRMWARE_IDS = {
    "v5.4.11": "422313",
    "v5.4.10": "371132",
}

try:
    from google.cloud import storage
except ImportError:
    storage = None

def _get_auth_headers():
    cookie_raw = ACTIVE_IAP_COOKIE.strip()
    uid_raw = ACTIVE_IAP_UID.strip()
    
    if "__Host-GCP_IAP_AUTH_TOKEN" in cookie_raw or "GCP_IAAP_AUTH" in cookie_raw:
        cookie_header = cookie_raw
    else:
        cookie_header = f"__Host-GCP_IAP_AUTH_TOKEN_A82A7FE83D3A1171={cookie_raw}; GCP_IAP_UID={uid_raw}"

    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_header
    }

def trigger_forge_build(config_name, fw_version, source="user", gitlab_ref="master", firmware_build_id=None):
    """
    Triggers a build job on Forge via POST to /configs/build.
    """
    if not ACTIVE_IAP_COOKIE:
        print("[FORGE SERVICE] Warning: ACTIVE_IAP_COOKIE missing. Skipping Forge trigger.")
        return False

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    headers = _get_auth_headers()

    clean_ver = fw_version.strip()
    version_str = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    stem = config_name.rsplit(".", 1)[0]
    ini_filename = f"{stem}.ini"
    
    build_id = firmware_build_id or FIRMWARE_IDS.get(version_str, "371132")

    payload = {
        "version": version_str,
        "cfg": stem,
        "config_name": stem,
        "partial_config_name": stem,
        "target_ini": ini_filename,
        "build_type": "firmware",
        "source": source,
        "firmware_source": "Release",
        "firmware_build_id": build_id,
        "gitlab_ref": gitlab_ref,
        "include_partials": "0"
    }

    try:
        print(f"[FORGE SERVICE] Submitting build for {stem} ({version_str}) to Forge...")
        response = requests.post(url, data=payload, headers=headers, timeout=30, allow_redirects=False)
        print(f"[FORGE SERVICE] Forge Status Code: {response.status_code}")
        
        if response.status_code in [200, 201, 302, 303]:
            build_job_url = response.headers.get("Location", "")
            print(f"[FORGE SERVICE] Build trigger submitted successfully! (Build Job: {build_job_url})")
            return True
        else:
            print(f"[FORGE SERVICE] Forge returned status {response.status_code}: {response.text[:300]}")
            return False
    except Exception as e:
        print(f"[FORGE SERVICE] Exception triggering Forge build: {e}")
        return False

def generate_partial_ini_content(form_data):
    """
    Constructs a partial INI text string containing only non-empty, overridden parameters.
    """
    ini_lines = []

    section_map = {
        "anti_passback": ("[card_tracker]", "enabled", {"enable": "true", "disable": "false"}),
        "ble": ("[ble]", "enabled", {"enable": "true", "disable": "false"}),
        "buzzer": ("[av]", "silence_beeper", {"enable": "false", "disable": "true"}),
        "idle_led": ("[av]", "idle_color", None),
        "keypad_bit": ("[wiegand]", "default_format", {"4": "4_bit", "8": "8_bit", "26": "26_bit"}),
        "osdp_address": ("[osdp/comms]", "addr", None),
        "osdp_baud_rate": ("[osdp/comms]", "baud_rate", None),
    }

    current_section = None
    for key, val in form_data.items():
        if not val or val == "":
            continue

        if key in section_map:
            sec, ini_key, val_dict = section_map[key]
            out_val = val_dict.get(val, val) if val_dict else val

            if current_section != sec:
                ini_lines.append(f"\n{sec}")
                current_section = sec
            ini_lines.append(f"{ini_key} = {out_val}")

    csn_keys = {
        "csn_mfc": "mifare_classic_format",
        "csn_ev1ev2": "mifare_desfire_format",
        "csn_iclass": "iso14443a_cl1_format",
        "csn_iso15693": "iso15693_format",
        "csn_iso14443a": "iso14443a_cl2_format"
    }

    csn_val_map = {
        "off": "0xFFFF",
        "on": "0x0000",
        "on_32": "0x2001",
        "on_56": "0x3800"
    }

    csn_header_added = False
    for k, ini_k in csn_keys.items():
        v = form_data.get(k)
        if v and v in csn_val_map:
            if not csn_header_added:
                ini_lines.append("\n[rfid/hf/app/csn]")
                csn_header_added = True
            ini_lines.append(f"{ini_k} = {csn_val_map[v]}")

    return "\n".join(ini_lines).strip()

def compile_partial_bin_via_forge(partial_name, fw_version, user_email="jhepburn@wavelynx.com", source="user", gitlab_ref="master", firmware_build_id=None, timeout_s=120):
    """
    1. Triggers Forge compilation job for a partial configuration profile.
    2. Polls GCS bucket subfolder (forge/{user_email}/v{version}/partials/) until compiled binary arrives.
    """
    if not ACTIVE_IAP_COOKIE:
        print("[FORGE SERVICE] Warning: ACTIVE_IAP_COOKIE missing. Skipping Forge trigger.")
        return False

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    headers = _get_auth_headers()

    clean_ver = fw_version.strip()
    version_str = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"
    stem = partial_name.rsplit(".", 1)[0] if "." in partial_name else partial_name
    ini_filename = f"{stem}.ini"
    build_id = firmware_build_id or FIRMWARE_IDS.get(version_str, "371132")
    user_email_clean = user_email.lower().strip()

    payload = {
        "version": version_str,
        "cfg": stem,
        "config_name": stem,
        "partial_config_name": stem,
        "target_ini": ini_filename,
        "build_type": "profile",
        "source": source,
        "firmware_source": "",
        "firmware_build_id": build_id,
        "gitlab_ref": gitlab_ref,  
        "include_partials": "1"
    }

    min_updated_time = datetime.now(timezone.utc)

    try:
        print(f"[FORGE SERVICE] Submitting partial build for {stem} ({version_str}) to Forge...")
        response = requests.post(url, data=payload, headers=headers, timeout=30, allow_redirects=False)
        print(f"[FORGE SERVICE] Forge Status Code: {response.status_code}")

        if response.status_code not in [200, 201, 302, 303]:
            print(f"[FORGE SERVICE] Forge returned status {response.status_code}: {response.text[:300]}")
            return False

        redirect_target = response.headers.get("Location", "")
        print(f"[FORGE SERVICE] Partial build job queued successfully! (Job URL: {redirect_target})")
        print(f"[FORGE SERVICE] Polling GCS for output binary...")

        if storage is not None:
            try:
                storage_client = storage.Client()
            except Exception:
                if "GOOGLE_APPLICATION_CREDENTIALS" in os.environ:
                    del os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
                storage_client = storage.Client()

            bucket_name = os.getenv("FORGE_GCS_BUCKET", "wavelynx_apex_config")
            prefix_path = f"forge/{user_email_clean}/{version_str}/partials/"

            start_time = time.time()
            clean_stem = stem.lower().strip()

            print(f"[GCS Polling] Scanning gs://{bucket_name}/{prefix_path} for '{clean_stem}'...")

            while time.time() - start_time < timeout_s:
                blobs = list(storage_client.list_blobs(bucket_name, prefix=prefix_path))
                sorted_blobs = sorted(blobs, key=lambda x: x.updated, reverse=True)

                for b in sorted_blobs:
                    filename = Path(b.name).name
                    filename_lower = filename.lower()

                    if b.size == 0 or b.updated < min_updated_time:
                        continue

                    if filename_lower.endswith(".bin") or filename_lower.endswith(".dck"):
                        print(f"[GCS Success] Found compiled Partial BIN: {b.name} ({b.size} bytes)")
                        return True

                time.sleep(3)

            print(f"[GCS Timeout] No compiled binary found after {timeout_s}s.")
            return False
        else:
            print("[FORGE SERVICE] Google Cloud Storage library not installed; skipping polling.")
            return True

    except Exception as e:
        print(f"[FORGE SERVICE] Exception triggering partial build: {e}")
        return False