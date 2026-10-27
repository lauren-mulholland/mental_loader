"""Run with Hermes' separate Python; register and call our plugin without a model."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
os.environ["HERMES_HOME"] = str(ROOT / ".local/hermes-home")
os.environ["HERMES_RUNTIME_DIR"] = str(ROOT / ".local/hermes-managed")


def main():
    import sys

    sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".local/hermes-home/.env", override=True)
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry

    manager = PluginManager()
    manager.discover_and_load()
    entries = {}
    for name in ("household_clock", "household_tasks", "household_remind"):
        entry = registry.get_entry(name, scope=manager.scope_key)
        if entry is None:
            raise RuntimeError(f"Plugin did not register {name}")
        entries[name] = entry
    clock = json.loads(entries["household_clock"].handler({}))
    assert clock["timezone"] == "Australia/Sydney"
    args = {
        "title": "Hermes native plugin feasibility",
        "due_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        "request_id": str(uuid4()),
    }
    saved = json.loads(entries["household_remind"].handler(args))
    assert "task_id" in saved, saved
    again = json.loads(entries["household_remind"].handler(args))
    assert saved["task_id"] == again["task_id"]
    tasks = json.loads(entries["household_tasks"].handler({}))
    assert any(task["id"] == saved["task_id"] for task in tasks)
    print(
        "PASS: real Hermes plugin manager registered all 3 tools; clock + "
        "reminder creation + idempotent retry + task listing against local PostgreSQL. "
        "No model calls made."
    )


if __name__ == "__main__":
    main()
