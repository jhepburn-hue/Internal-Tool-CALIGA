import os
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from services.gcs_client import get_gcs_client, forge_root
from utils.versions import normalize_fw_version
from utils.errors import UserFacingError
from utils.csn import CSN_OFF, CSN_NO_OVERRIDE, CSN_32_MSB, CSN_56_LSB, CSN_56_MSB

log = logging.getLogger(__name__)

FORGE_BASE_URL = os.getenv('FORGE_BASE_URL', 'https://forge.wavelynxdev.com')
ACTIVE_IAP_COOKIE = os.getenv('ACTIVE_IAP_COOKIE', '')
ACTIVE_IAP_UID = os.getenv('ACTIVE_IAP_UID', '112564004525954034965')

FIRMWARE_IDS = {
    "v5.4.11": "422313",
    "v5.4.10": "371132",
}


class ForgeAuthError(UserFacingError):
    """Forge rejected the IAP cookie, or no cookie is configured. The message is shown to the user."""


IAP_EXPIRED_MESSAGE = (
    "Forge rejected the IAP cookie, so no build was started. The cookie has probably expired. "
    "Open Forge in your browser, copy a fresh __Host-GCP_IAP_AUTH_TOKEN cookie into ACTIVE_IAP_COOKIE "
    "in .env, then restart CALIGA."
)


def _require_iap_cookie():
    if not ACTIVE_IAP_COOKIE:
        raise ForgeAuthError("ACTIVE_IAP_COOKIE is not set in .env, so CALIGA can't start Forge builds.")


def _check_iap_auth(response):
    """IAP answers an expired cookie with 401/403, or a redirect to Google sign-in instead of a build job."""
    location = response.headers.get("Location", "")
    redirected_to_sign_in = response.status_code in (301, 302, 303, 307) and (
        "accounts.google.com" in location or "/_gcp_iap/" in location
    )
    if response.status_code in (401, 403) or redirected_to_sign_in:
        log.error("Forge rejected the IAP cookie (status %s, Location: %s).", response.status_code, location[:120])
        raise ForgeAuthError(IAP_EXPIRED_MESSAGE)


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


def _firmware_build_id(version_str):
    build_id = FIRMWARE_IDS.get(version_str)
    if not build_id:
        log.error("No Forge firmware build ID configured for %s.", version_str)
    return build_id


def trigger_forge_build(config_name, fw_version, source="user", gitlab_ref="master"):
    """
    Triggers a build job on Forge via POST to /configs/build.
    Raises ForgeAuthError when the IAP cookie is missing or rejected.
    """
    _require_iap_cookie()

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    headers = _get_auth_headers()

    version_str, _ = normalize_fw_version(fw_version)
    stem = config_name.rsplit(".", 1)[0]
    ini_filename = f"{stem}.ini"

    build_id = _firmware_build_id(version_str)
    if not build_id:
        return False

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
        log.info("Submitting build for %s (%s) to Forge...", stem, version_str)
        response = requests.post(url, data=payload, headers=headers, timeout=30, allow_redirects=False)
        _check_iap_auth(response)

        if response.status_code in [200, 201, 302, 303]:
            log.info("Build trigger submitted (Build Job: %s)", response.headers.get("Location", ""))
            return True

        log.error("Forge returned status %s: %s", response.status_code, response.text[:300])
        return False
    except ForgeAuthError:
        raise
    except Exception as e:
        log.exception("Exception triggering Forge build: %s", e)
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

    # Same keys per row as CSN_ROWS in services/ini_builder.py.
    csn_keys = {
        "csn_mfc": ["mifare_classic_format"],
        "csn_ev1ev2": ["mifare_desfire_format", "mifare_plus_format", "mifare_ultralight_format"],
        "csn_iclass": ["pico15693_format"],
        "csn_iso15693": ["iso15693_format"],
        "csn_iso14443a": ["iso14443a_cl1_format", "iso14443a_cl2_format", "iso14443b_format", "felica_format"],
    }

    csn_val_map = {
        "off": CSN_OFF,
        "on": CSN_NO_OVERRIDE,
        "on_32": CSN_32_MSB,
        "on_56": CSN_56_MSB,
        "on_56_lsb": CSN_56_LSB,
    }

    csn_header_added = False
    for k, ini_keys in csn_keys.items():
        v = form_data.get(k)
        if v and v in csn_val_map:
            if not csn_header_added:
                ini_lines.append("\n[rfid/hf/app/csn]")
                csn_header_added = True
            ini_lines.extend(f"{ini_k} = {csn_val_map[v]}" for ini_k in ini_keys)

    return "\n".join(ini_lines).strip()


def compile_partial_bin_via_forge(partial_name, fw_version, user_email, output_dir, source="user", gitlab_ref="master", timeout_s=120):
    """
    1. Triggers Forge compilation job for a partial configuration profile.
    2. Polls GCS (forge/<FORGE_FOLDER_EMAIL>/v{version}/partials/) until the compiled binary arrives.
    3. Downloads it into output_dir and returns the local path, or None on failure.
    Raises ForgeAuthError when the IAP cookie is missing or rejected.
    """
    _require_iap_cookie()

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    headers = _get_auth_headers()

    version_str, _ = normalize_fw_version(fw_version)
    stem = partial_name.rsplit(".", 1)[0] if "." in partial_name else partial_name
    ini_filename = f"{stem}.ini"
    build_id = _firmware_build_id(version_str)
    if not build_id:
        return None

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
        log.info("Submitting partial build for %s (%s) to Forge...", stem, version_str)
        response = requests.post(url, data=payload, headers=headers, timeout=30, allow_redirects=False)
        _check_iap_auth(response)

        if response.status_code not in [200, 201, 302, 303]:
            log.error("Forge returned status %s: %s", response.status_code, response.text[:300])
            return None

        log.info("Partial build job queued (Job URL: %s). Polling GCS for output binary...", response.headers.get("Location", ""))

        storage_client = get_gcs_client()
        if storage_client is None:
            log.error("GCS client unavailable. Can't poll for the partial binary.")
            return None

        bucket_name = os.getenv("FORGE_GCS_BUCKET", "wavelynx_apex_config")
        prefix_path = f"{forge_root()}{version_str}/partials/"
        clean_stem = stem.lower().strip()

        start_time = time.time()
        while time.time() - start_time < timeout_s:
            fresh = [
                b for b in storage_client.list_blobs(bucket_name, prefix=prefix_path)
                if b.size and b.updated >= min_updated_time
                and Path(b.name).name.lower().endswith((".bin", ".dck"))
            ]
            # Prefer a file named after this partial; otherwise take the newest binary.
            fresh.sort(key=lambda b: (clean_stem in Path(b.name).name.lower(), b.updated), reverse=True)

            if fresh:
                blob = fresh[0]
                local_path = os.path.join(output_dir, Path(blob.name).name)
                blob.download_to_filename(local_path)
                log.info("Found compiled partial BIN: %s (%s bytes)", blob.name, blob.size)
                return local_path

            time.sleep(3)

        log.error("No compiled partial binary found after %ss.", timeout_s)
        return None

    except ForgeAuthError:
        raise
    except Exception as e:
        log.exception("Exception building partial: %s", e)
        return None
