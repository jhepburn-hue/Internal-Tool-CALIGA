import os
import requests

JIRA_DOMAIN = os.getenv('JIRA_DOMAIN', 'wavelynx.atlassian.net')
JIRA_USER_EMAIL = os.getenv('JIRA_USER_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'CAL')

def create_jira_issue(summary, description, issue_type="Internal Bug", project_key=None):
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
        if response.status_code in [200, 201]:
            data = response.json()
            issue_key = data.get('key')
            issue_id = data.get('id')
            print(f"[JIRA SERVICE] Created Jira issue: {issue_key}")

            transition_to_next_up(issue_id, auth, headers)
            return issue_key
        else:
            print(f"[JIRA SERVICE] Failed to create Jira issue ({response.status_code}): {response.text}")
            return None
    except Exception as e:
        print(f"[JIRA SERVICE] Exception while creating Jira issue: {e}")
        return None

def transition_to_next_up(issue_id, auth, headers):
    """
    Finds and applies the transition ID corresponding to 'Next Up'.
    """
    try:
        trans_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_id}/transitions"
        res = requests.get(trans_url, auth=auth, headers=headers, timeout=5)
        if res.status_code == 200:
            transitions = res.json().get('transitions', [])
            next_up_trans = next((t for t in transitions if t.get('name', '').lower() == 'next up'), None)
            if next_up_trans:
                trans_id = next_up_trans['id']
                requests.post(trans_url, json={"transition": {"id": trans_id}}, auth=auth, headers=headers, timeout=5)
                print(f"[JIRA SERVICE] Transitioned issue {issue_id} to 'Next Up'.")
    except Exception as e:
        print(f"[JIRA SERVICE] Exception during transition: {e}")

def create_failure_ticket(config_name, user_email, fw_version, failure_details):
    """
    Creates an 'Internal Bug' Jira ticket in 'Next Up' column for failed test groups.
    """
    summary = f"Test Failure: {config_name} on FW {fw_version}"
    description = (
        f"Configuration '{config_name}' failed testing on Firmware {fw_version}.\n\n"
        f"Tested By: {user_email}\n"
        f"Failed Criteria:\n{failure_details}"
    )
    return create_jira_issue(summary, description, issue_type="Internal Bug")