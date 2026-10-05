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

def search_jira_issue_by_config(config_name, fw_version):
    """
    Searches Jira JQL using key search terms (config_name + fw_version).
    """
    if not JIRA_USER_EMAIL or not JIRA_API_TOKEN:
        print("[JIRA SERVICE] API credentials missing. Skipped Jira search.")
        return None

    url = f"https://{JIRA_DOMAIN}/rest/api/3/search"
    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
    jql = f'project = "{JIRA_PROJECT_KEY}" AND text ~ "{config_name}" ORDER BY created DESC'
    
    try:
        res = requests.get(url, params={"jql": jql, "maxResults": 5}, auth=auth, headers=headers, timeout=5)
        if res.status_code == 200:
            issues = res.json().get('issues', [])
            for issue in issues:
                summary = issue.get('fields', {}).get('summary', '')
                if config_name in summary and fw_version in summary:
                    found_key = issue.get('key')
                    print(f"[JIRA SERVICE] Matched Jira ticket {found_key} for '{summary}'")
                    return found_key
            if issues:
                found_key = issues[0].get('key')
                print(f"[JIRA SERVICE] Fallback match Jira ticket {found_key} for config {config_name}")
                return found_key
    except Exception as e:
        print(f"[JIRA SERVICE] Exception searching Jira JQL: {e}")
        
    return None

def assign_jira_ticket(issue_key, assignee_email=None):
    """
    Assigns or unassigns a Jira issue directly using its exact key (e.g., SWAG-126).
    """
    if not JIRA_USER_EMAIL or not JIRA_API_TOKEN or not issue_key:
        print(f"[JIRA SERVICE] Assignment skipped. Key or credentials missing.")
        return False

    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    account_id = None
    if assignee_email:
        try:
            user_url = f"https://{JIRA_DOMAIN}/rest/api/3/user/search?query={assignee_email}"
            u_res = requests.get(user_url, auth=auth, headers=headers, timeout=5)
            if u_res.status_code == 200 and u_res.json():
                users = u_res.json()
                matched_user = next((u for u in users if u.get('emailAddress', '').lower() == assignee_email.lower()), users[0])
                account_id = matched_user.get('accountId')
        except Exception as e:
            print(f"[JIRA SERVICE] Exception resolving account ID: {e}")

    assign_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/assignee"
    payload = {"accountId": account_id} if account_id else {"accountId": "-1"}

    try:
        res = requests.put(assign_url, json=payload, auth=auth, headers=headers, timeout=5)
        if res.status_code in [200, 204]:
            action_str = f"assigned to {account_id}" if account_id else "unassigned"
            print(f"[JIRA SERVICE] Successfully {action_str} Jira ticket {issue_key}.")
            return True
        else:
            print(f"[JIRA SERVICE] Failed to update Jira ticket {issue_key} ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"[JIRA SERVICE] Exception updating Jira ticket assignment: {e}")
        return False

def transition_jira_issue_to_complete(issue_key):
    """
    Finds and applies the transition ID corresponding to 'Complete' or 'Done'.
    """
    if not JIRA_USER_EMAIL or not JIRA_API_TOKEN or not issue_key:
        print(f"[JIRA SERVICE] Credentials or issue_key missing. Skipped transition for {issue_key}.")
        return False

    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    trans_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/transitions"

    try:
        res = requests.get(trans_url, auth=auth, headers=headers, timeout=5)
        if res.status_code == 200:
            transitions = res.json().get('transitions', [])
            
            complete_trans = next(
                (t for t in transitions if t.get('name', '').lower() in ['complete', 'done', 'resolved', 'closed']), 
                None
            )

            if complete_trans:
                trans_id = complete_trans['id']
                trans_res = requests.post(
                    trans_url, 
                    json={"transition": {"id": trans_id}}, 
                    auth=auth, 
                    headers=headers, 
                    timeout=5
                )
                if trans_res.status_code in [200, 204]:
                    print(f"[JIRA SERVICE] Successfully transitioned Jira ticket {issue_key} to 'Complete'.")
                    return True
                else:
                    print(f"[JIRA SERVICE] Failed to transition Jira ticket {issue_key} ({trans_res.status_code}): {trans_res.text}")
            else:
                available_names = [t.get('name') for t in transitions]
                print(f"[JIRA SERVICE] Could not find 'Complete' transition for {issue_key}. Available transitions: {available_names}")
    except Exception as e:
        print(f"[JIRA SERVICE] Exception during transition to complete: {e}")

    return False