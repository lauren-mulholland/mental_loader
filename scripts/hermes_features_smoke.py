"""Test native memory persistence and a no-agent cron execution without model calls."""

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))
os.environ["HERMES_HOME"] = str(ROOT / ".local/hermes-home")
os.environ["HERMES_RUNTIME_DIR"] = str(ROOT / ".local/hermes-managed")


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".local/hermes-home/.env", override=True)
    home = os.environ["HERMES_HOME"]
    # Synthetic preference only; never add test memories to the user's real profile.
    from tools.memory_tool import load_on_disk_store, memory_tool

    with tempfile.TemporaryDirectory(dir=ROOT / ".local", prefix="memory-check-") as isolated:
        os.environ["HERMES_HOME"] = isolated
        result = json.loads(
            memory_tool(
                action="add",
                target="user",
                content="This test household shops on Saturdays.",
                store=load_on_disk_store(),
            )
        )
        assert result.get("success"), "Native memory tool did not confirm the write"
        fresh = load_on_disk_store()
        assert any("Saturdays" in entry for entry in fresh.user_entries)
        with tempfile.TemporaryDirectory(dir=ROOT / ".local", prefix="other-memory-") as other:
            os.environ["HERMES_HOME"] = other
            assert not load_on_disk_store().user_entries
    os.environ["HERMES_HOME"] = home

    from cron.jobs import list_jobs
    from cron.scheduler import run_job
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry

    manager = PluginManager()
    manager.discover_and_load()
    remind = registry.get_entry("household_remind", scope=manager.scope_key)
    tasks = registry.get_entry("household_tasks", scope=manager.scope_key)
    saved = json.loads(
        remind.handler(
            {
                "title": "Native Hermes scheduler feasibility",
                "due_at": (datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
                "request_id": str(uuid4()),
            }
        )
    )
    assert "task_id" in saved, saved
    time.sleep(3)
    job = next(
        job
        for job in list_jobs(include_disabled=True)
        if job.get("name") == "mental-loader-reminders"
    )
    assert job["no_agent"] is True
    success, _output, _reply, error = run_job(job)
    assert success, error
    task = next(task for task in json.loads(tasks.handler({})) if task["id"] == saved["task_id"])
    assert task["notified_at"] is not None
    print(
        "PASS: native Hermes memory tool persisted a preference across store reloads; "
        "a separate home did not see it; native no-agent cron executed the household "
        "dispatch script and processed a due reminder. No model calls made."
    )


if __name__ == "__main__":
    main()
