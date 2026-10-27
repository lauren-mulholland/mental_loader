"""Web Push transport. Notification content deliberately omits household details."""

import json
from urllib.parse import urlparse

from pywebpush import WebPushException, webpush


def validate_endpoint(endpoint: str) -> str:
    url = urlparse(endpoint)
    host = (url.hostname or "").lower()
    allowed = (
        host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"}
        or host == "web.push.apple.com"
        or host.endswith(".push.apple.com")
        or host.endswith(".notify.windows.com")
    )
    if not allowed or url.scheme != "https" or url.port not in (None, 443):
        raise ValueError("Use a subscription from a supported browser push service.")
    if url.username or url.password or url.fragment:
        raise ValueError("Invalid push endpoint.")
    return endpoint


def send_push(subscription: dict, task_id: str, conversation_id: str, settings):
    validate_endpoint(subscription["endpoint"])
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(
                {
                    "title": "Mental Loader",
                    "body": "A household reminder is ready. Open to view it.",
                    "url": f"/?conversation={conversation_id}",
                    "tag": task_id,
                }
            ),
            vapid_private_key=str(settings.vapid_private_key_path),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=3600,
            timeout=10,
        )
    except WebPushException as error:
        if error.response is not None and error.response.status_code in (404, 410):
            return "expired"
        raise
    return "sent"
