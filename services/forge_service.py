import os
import requests

FORGE_BASE_URL = os.getenv('FORGE_BASE_URL', 'https://forge.wavelynxdev.com')
ACTIVE_IAP_COOKIE = os.getenv('ACTIVE_IAP_COOKIE')

def trigger_forge_build(config_name, fw_version, source="user", gitlab_ref="master", firmware_build_id=None):
    """
    Triggers a build job on Forge via POST to /configs/build.
    - If firmware_build_id is provided, Forge builds DCK envelopes.
    """
    if not ACTIVE_IAP_COOKIE:
        print("[FORGE SERVICE] Warning: ACTIVE_IAP_COOKIE missing. Skipping Forge trigger.")
        return False

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    
    clean_ver = fw_version.strip()
    version_str = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": ACTIVE_IAP_COOKIE
    }

    payload = {
        "version": version_str,
        "source": source,
        "config_name": config_name,
        "gitlab_ref": gitlab_ref
    }

    if firmware_build_id:
        payload["firmware_build_id"] = firmware_build_id

    try:
        print(f"[FORGE SERVICE] Submitting build for {config_name} ({version_str}) to Forge...")
        response = requests.post(url, data=payload, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code in [200, 201, 302]:
            print(f"[FORGE SERVICE] Build trigger submitted successfully! (Status: {response.status_code})")
            return True
        else:
            print(f"[FORGE SERVICE] Forge returned status {response.status_code}: {response.text[:200]}")
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


def compile_partial_bin_via_forge(partial_name, fw_version, user_email):
    """
    Triggers Forge build service for partial configs:
    forge/{user_email}/{fw_version}/partials/
    """
    if not ACTIVE_IAP_COOKIE:
        print("[FORGE SERVICE] Warning: ACTIVE_IAP_COOKIE missing. Skipping Forge trigger.")
        return False

    url = f"{FORGE_BASE_URL.rstrip('/')}/configs/build"
    
    clean_ver = fw_version.strip()
    version_str = clean_ver if clean_ver.startswith('v') else f"v{clean_ver}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": ACTIVE_IAP_COOKIE
    }

    payload = {
        "version": version_str,
        "source": "user",
        "config_name": partial_name,
        "include_partials": "1",
        "is_partial": "1"
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code in [200, 201, 302]:
            print(f"[FORGE SERVICE] Partial build triggered for {partial_name}")
            return True
        else:
            print(f"[FORGE SERVICE] Partial build failed with status {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"[FORGE SERVICE] Exception triggering partial build: {e}")
        return False