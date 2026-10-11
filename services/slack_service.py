import os
import logging
import requests

log = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

def send_alert_qa_notification(config_name, status, user_email, fw_version='v5.4.10'):
    """
    Sends an Alert QA notification message to Slack via Webhook.
    """
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL not configured in environment.")
        return False

    message_text = f"*QA Test Request*\n*Configuration:* `{config_name}`\n*Target FW:* `{fw_version}`\n*Status:* `{status}`\n*Requested By:* {user_email}\n_Please review and execute assigned test validation criteria._"

    payload = {
        "text": message_text,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "QA Test Request Triggered",
                    "emoji": False
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Configuration:*\n`{config_name}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Target FW:*\n`{fw_version}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Status:*\n`{status}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Requested By:*\n{user_email}"
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code in [200, 201]:
            log.info("Notification sent for %s (%s).", config_name, fw_version)
            return True
        else:
            log.error("Slack webhook returned %s: %s", response.status_code, response.text)
            return False
    except Exception as e:
        log.exception("Exception sending Slack notification: %s", e)
        return False


def send_failure_message(config_name, user_email, fw_run_ver, criteria_details, comments=""):
    """
    Sends a test failure notification tagging SET users in Slack via Webhook.
    """
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL not configured in environment.")
        return False

    message_text = f"*Test Failure Alert*\n*Configuration:* `{config_name}`\n*FW Run:* `{fw_run_ver}`\n*Tested By:* {user_email}\n*Failed Criteria:* {criteria_details}\n_Tagging SET team for immediate review._"

    payload = {
        "text": message_text,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Test Suite Failure Detected",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Configuration:*\n`{config_name}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*FW Version:*\n`{fw_run_ver}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Tested By:*\n{user_email}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Failure Details & Comments:*\n{criteria_details}"
                }
            }
        ]
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code in [200, 201]:
            log.info("Failure notification sent for %s.", config_name)
            return True
        else:
            log.error("Slack webhook returned %s: %s", response.status_code, response.text)
            return False
    except Exception as e:
        log.exception("Exception sending failure notification: %s", e)
        return False