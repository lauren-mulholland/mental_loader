"""Opt-in, two-turn provider smoke check through the PWA backend. Costs model credits."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from mental_loader.config import Settings


def wait_for_reply(client, conversation, previous_count, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/conversations/{conversation}/messages")
        response.raise_for_status()
        data = response.json()
        if not data["active_runs"] and len(data["messages"]) > previous_count:
            return data["messages"][-1]["content"]
        time.sleep(2)
    raise RuntimeError("Request did not finish; inspect saved work before retrying")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_model_calls:
        raise SystemExit("This check spends provider credits. Pass --allow-model-calls to run it.")
    settings = Settings()
    codes = json.loads(Path(".local/access-codes.json").read_text())
    with httpx.Client(
        base_url=settings.public_origin.rstrip("/"),
        timeout=10,
        headers={"Origin": settings.public_origin.rstrip("/")},
        follow_redirects=False,
    ) as client:
        result = client.post(
            "/api/login", json={"access_code": codes["My household"]["access_code"]}
        )
        result.raise_for_status()
        status = client.get("/api/me")
        status.raise_for_status()
        if not status.json()["ai_enabled"]:
            raise SystemExit(
                "Enable AI_ENABLED after choosing your provider, then restart the app."
            )
        result = client.post("/api/conversations")
        result.raise_for_status()
        conversation = result.json()["id"]
        reference = "cedar-" + uuid4().hex[:8]
        prompts = [
            f"For this temporary session check my reference word is {reference}. "
            "Acknowledge briefly. Do not save it to long-term memory or use tools.",
            "What reference word did I give earlier in this conversation? Reply with it only. "
            "Do not use tools or write long-term memory.",
        ]
        count, reply = 0, ""
        for prompt in prompts:
            result = client.post(
                f"/api/conversations/{conversation}/messages",
                json={
                    "content": prompt,
                    "request_id": str(uuid4()),
                },
            )
            result.raise_for_status()
            reply = wait_for_reply(client, conversation, count)
            count += 2
        if reference not in reply:
            raise SystemExit(
                "Continuity was not confirmed. Inspect the saved test chat and Hermes logs."
            )
        Path(".local/live-chat-check.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "conversation_id": conversation,
                    "top_level_requests": 2,
                    "reference_recalled": True,
                },
                indent=2,
            )
            + "\n"
        )
        print(
            "PASS: two live turns through the PWA backend; prior-session context recalled. "
            "This checks conversation continuity, not cross-session memory or specialist quality."
        )


if __name__ == "__main__":
    main()
