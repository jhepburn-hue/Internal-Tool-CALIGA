import os
import requests

PB_BASE_URL = os.getenv('PB_BASE_URL', 'http://127.0.0.1:8090')
PB_COLLECTION_ID = os.getenv('PB_COLLECTION_ID', 'keysets')
PB_ADMIN_TOKEN = os.getenv('PB_ADMIN_TOKEN')

def get_pocketbase_tokens(keyset_id):
    """
    Fetches mobile credential tokens from PocketBase collection using admin auth.
    """
    if not PB_ADMIN_TOKEN:
        print("[POCKETBASE SERVICE] Admin token missing. Using fallback token.")
        return f"PB_TOKEN_{keyset_id}_MOCK_8892"

    try:
        url = f"{PB_BASE_URL}/api/collections/{PB_COLLECTION_ID}/records"
        headers = {"Authorization": f"Bearer {PB_ADMIN_TOKEN}"}
        params = {"filter": f"keyset_id='{keyset_id}'"}
        
        response = requests.get(url, headers=headers, params=params, timeout=4)
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            if items:
                print(f"[POCKETBASE SERVICE] Token retrieved for {keyset_id}")
                return items[0].get('token', 'PB_TOKEN_SAMPLE_12345')
    except Exception as e:
        print(f"[POCKETBASE SERVICE] Error querying PocketBase: {e}")

    return f"PB_TOKEN_{keyset_id}_MOCK_8892"