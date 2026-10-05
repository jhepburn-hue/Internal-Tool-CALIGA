import os
import time
import requests

FORGE_BASE_URL = os.getenv('FORGE_BASE_URL', 'https://forge.wavelynxdev.com')
ACTIVE_IAP_UID = os.getenv('ACTIVE_IAP_UID')
ACTIVE_IAP_COOKIE = os.getenv('ACTIVE_IAP_COOKIE')

def trigger_forge_build(config_name, fw_version, source="user", gitlab_ref="master"):
    """
    Triggers a profile build job on Forge via POST to /configs/build.
    - source: 'user' (for forge/jhepburn@wavelynx.com/v5.4.11/) or 'master' (for input/v5.4.11/)
    """
    if not ACTIVE_IAP_COOKIE:
        print("[FORGE SERVICE] Warning: ACTIVE_IAP_COOKIE missing. Skipping real Forge trigger.")
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