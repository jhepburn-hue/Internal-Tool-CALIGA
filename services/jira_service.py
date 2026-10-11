import os
import logging
import requests

log = logging.getLogger(__name__)

JIRA_DOMAIN = os.getenv('JIRA_DOMAIN', 'wavelynx.atlassian.net')
JIRA_USER_EMAIL = os.getenv('JIRA_USER_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY')

HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


def _auth():
    return (JIRA_USER_EMAIL, JIRA_API_TOKEN)


def _has_credentials():
    return bool(JIRA_USER_EMAIL and JIRA_API_TOKEN)


def jira_browse_url(issue_key):
    return f"https://{JIRA_DOMAIN}/browse/{issue_key}" if issue_key else None


def create_jira_issue(summary, description, issue_type="Internal Bug", project_key=None):
    """Creates a Jira issue and returns its key, or None when creation fails."""
    pkey = project_key or JIRA_PROJECT_KEY
    if not _has_credentials() or not pkey:
        log.warning("Jira credentials or JIRA_PROJECT_KEY missing. No ticket created.")
        return None

    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue"

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
        response = requests.post(url, json=payload, auth=_auth(), headers=HEADERS, timeout=5)
        if response.status_code in [200, 201]:
            data = response.json()
            issue_key = data.get('key')
            log.info("Created Jira issue %s", issue_key)

            transition_to_next_up(data.get('id'))
            return issue_key

        log.error("Failed to create Jira issue (%s): %s", response.status_code, response.text)
        return None
    except Exception as e:
        log.exception("Exception while creating Jira issue: %s", e)
        return None


def transition_to_next_up(issue_id):
    """
    Finds and applies the transition ID corresponding to 'Next Up'.
    """
    try:
        trans_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_id}/transitions"
        res = requests.get(trans_url, auth=_auth(), headers=HEADERS, timeout=5)
        if res.status_code == 200:
            transitions = res.json().get('transitions', [])
            next_up_trans = next((t for t in transitions if t.get('name', '').lower() == 'next up'), None)
            if next_up_trans:
                requests.post(trans_url, json={"transition": {"id": next_up_trans['id']}}, auth=_auth(), headers=HEADERS, timeout=5)
                log.info("Transitioned issue %s to 'Next Up'.", issue_id)
    except Exception as e:
        log.exception("Exception during transition: %s", e)


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


def assign_jira_ticket(issue_key, assignee_email=None):
    """
    Assigns or unassigns a Jira issue directly using its exact key (e.g., SWAG-126).
    Assignment only happens when a Jira user's email matches exactly.
    """
    if not _has_credentials() or not issue_key:
        log.warning("Jira assignment skipped. Key or credentials missing.")
        return False

    account_id = None
    if assignee_email:
        try:
            user_url = f"https://{JIRA_DOMAIN}/rest/api/3/user/search"
            u_res = requests.get(user_url, params={"query": assignee_email}, auth=_auth(), headers=HEADERS, timeout=5)
            if u_res.status_code == 200:
                matched_user = next(
                    (u for u in u_res.json() if (u.get('emailAddress') or '').lower() == assignee_email.lower()),
                    None
                )
                account_id = matched_user.get('accountId') if matched_user else None
        except Exception as e:
            log.exception("Exception resolving account ID: %s", e)

        if not account_id:
            log.warning("No Jira user matches %s exactly. Ticket %s left unchanged.", assignee_email, issue_key)
            return False

    assign_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/assignee"
    payload = {"accountId": account_id} if account_id else {"accountId": None}

    try:
        res = requests.put(assign_url, json=payload, auth=_auth(), headers=HEADERS, timeout=5)
        if res.status_code in [200, 204]:
            log.info("Jira ticket %s %s.", issue_key, f"assigned to {account_id}" if account_id else "unassigned")
            return True

        log.error("Failed to update Jira ticket %s (%s): %s", issue_key, res.status_code, res.text)
        return False
    except Exception as e:
        log.exception("Exception updating Jira ticket assignment: %s", e)
        return False


def transition_jira_issue_to_complete(issue_key):
    """
    Finds and applies the transition ID corresponding to 'Complete' or 'Done'.
    """
    if not _has_credentials() or not issue_key:
        log.warning("Credentials or issue_key missing. Skipped transition for %s.", issue_key)
        return False

    trans_url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/transitions"

    try:
        res = requests.get(trans_url, auth=_auth(), headers=HEADERS, timeout=5)
        if res.status_code == 200:
            transitions = res.json().get('transitions', [])

            complete_trans = next(
                (t for t in transitions if t.get('name', '').lower() in ['complete', 'done', 'resolved', 'closed']),
                None
            )

            if complete_trans:
                trans_res = requests.post(
                    trans_url,
                    json={"transition": {"id": complete_trans['id']}},
                    auth=_auth(),
                    headers=HEADERS,
                    timeout=5
                )
                if trans_res.status_code in [200, 204]:
                    log.info("Transitioned Jira ticket %s to 'Complete'.", issue_key)
                    return True
                log.error("Failed to transition Jira ticket %s (%s): %s", issue_key, trans_res.status_code, trans_res.text)
            else:
                available_names = [t.get('name') for t in transitions]
                log.error("No 'Complete' transition for %s. Available transitions: %s", issue_key, available_names)
    except Exception as e:
        log.exception("Exception during transition to complete: %s", e)

    return False
