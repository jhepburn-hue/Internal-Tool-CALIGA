import os
import requests

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

def send_alert_qa_notification(config_name, status, user_email, fw_version='v5.4.10'):
    """
    Sends an Alert QA notification message to Slack via Webhook.
    """
    if not SLACK_WEBHOOK_URL:
        print("[SLACK SERVICE] Warning: SLACK_WEBHOOK_URL not configured in environment.")
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
            print(f"[SLACK SERVICE] Notification sent for {config_name} ({fw_version}).")
            return True
        else:
            print(f"[SLACK SERVICE] Slack webhook returned {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"[SLACK SERVICE] Exception sending Slack notification: {e}")
        return False