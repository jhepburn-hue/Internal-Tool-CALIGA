import os
import requests

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

def send_alert_qa_notification(config_name, status, user_email):
    """
    Sends a formatted Slack notification tagging QA that testing is needed for a configuration.
    """
    if not SLACK_WEBHOOK_URL:
        print("[SLACK SERVICE] SLACK_WEBHOOK_URL not set in environment. Skipping notification.")
        return False

    payload = {
        "text": f"*QA Testing Alert triggered for {config_name}*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*:warning: QA Testing Needed:* `<{config_name}>`\n"
                            f"*Current Status:* `{status}`\n"
                            f"*Requested By:* {user_email}\n"
                            f"Please review the configuration parameters in CALIGA and initiate a Firmware Test Run."
                }
            }
        ]
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"[SLACK SERVICE] Successfully alerted QA for {config_name}")
            return True
        else:
            print(f"[SLACK SERVICE] Failed to send Slack alert. Status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"[SLACK SERVICE] Exception while sending Slack alert: {e}")
        return False