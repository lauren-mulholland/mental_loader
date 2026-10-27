"""Observe the running native Hermes scheduler; never call its runner directly."""

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from mental_loader.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=150)
    args = parser.parse_args()
    settings = Settings()
    codes = json.loads(Path(".local/access-codes.json").read_text())
    headers = {"Authorization": "Bearer " + codes["My household"]["access_code"]}
    with httpx.Client(
        base_url=settings.public_origin.rstrip("/"), timeout=10, follow_redirects=False
    ) as client:
        response = client.post(
            "/api/tool/reminders",
            headers=headers,
            json={
                "title": "Native scheduler cadence check",
                "due_at": (datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
                "request_id": str(uuid4()),
            },
        )
        response.raise_for_status()
        task_id = response.json()["task_id"]
        started = time.monotonic()
        while time.monotonic() - started < args.timeout:
            result = client.get("/api/tool/tasks", headers=headers)
            result.raise_for_status()
            task = next(task for task in result.json() if task["id"] == task_id)
            if task["notified_at"]:
                elapsed = round(time.monotonic() - started, 1)
                evidence = {
                    "status": "passed",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": elapsed,
                    "task_id": task_id,
                    "native_runner_called_by_test": False,
                    "push_display_verified": False,
                }
                Path(".local/scheduler-check.json").write_text(
                    json.dumps(evidence, indent=2) + "\n"
                )
                print(
                    f"PASS: native scheduler processed a due reminder after {elapsed}s. "
                    "No direct dispatch or model calls; phone push display remains unverified."
                )
                return
            time.sleep(3)
    raise SystemExit("No scheduled dispatch observed. Check Hermes cron status and backend logs.")


if __name__ == "__main__":
    main()
