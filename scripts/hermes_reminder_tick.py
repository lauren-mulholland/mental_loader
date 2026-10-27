"""One portable dispatch action, invoked by Hermes' no-agent cron scheduler."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main():
    origin = os.environ.get("ML_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("ML_HOUSEHOLD_ACCESS_CODE", "")
    url = urlparse(origin)
    if len(token) < 32 or url.username or url.password or url.scheme not in {"http", "https"}:
        raise ValueError("Missing or invalid household dispatch configuration")
    if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Plain HTTP requires a loopback backend")
    request = Request(
        origin + "/api/tool/dispatch-reminders",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=45) as response:
            if not json.load(response).get("ok"):
                raise ValueError("Dispatch did not confirm success")
    except (HTTPError, URLError, TimeoutError, ValueError):
        print(
            "Household dispatch failed; check backend availability and tool credentials.",
            file=sys.stderr,
        )
        return 1
    # The PWA transport already received the action; avoid a second cron output delivery.
    print(json.dumps({"wakeAgent": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
