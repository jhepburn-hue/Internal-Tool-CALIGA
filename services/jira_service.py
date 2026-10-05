import os
import requests

JIRA_DOMAIN = os.getenv('JIRA_DOMAIN', 'wavelynx.atlassian.net')
JIRA_USER_EMAIL = os.getenv('JIRA_USER_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'CAL')

def create_jira_issue(summary, description, issue_type="Bug", project_key=None):
    pkey = project_key or JIRA_PROJECT_KEY
    if not JIRA_USER_EMAIL or not JIRA_API_TOKEN:
        print("[JIRA SERVICE] API credentials missing. Simulating ticket creation.")
        return "CALIGA-101"

    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue"
    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    payload = {
        "fields": {
            "project": {"key": pkey},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}]
                    }
                ]
            },
            "issuetype": {"name": issue_type}
        }
    }

    try:
        response = requests.post(url, json=payload, auth=auth, headers=headers, timeout=5)
        if response.status_code == 201:
            data = response.json()
            issue_key = data.get('key')
            print(f"[JIRA SERVICE] Created Jira issue: {issue_key}")
            return issue_key
        else:
            print(f"[JIRA SERVICE] Failed to create Jira issue. Response: {response.text}")
            return None
    except Exception as e:
        print(f"[JIRA SERVICE] Exception while creating Jira issue: {e}")
        return None