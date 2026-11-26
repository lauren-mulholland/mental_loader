"""Real gateway health, native cron and fresh-volume restore; synthetic state, no inference."""

import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from runtime_box import provision

from mental_loader.backups import export_runtime_home

WRITE = r"""
from tools.memory_tool import memory_tool, load_on_disk_store
from hermes_state import SessionDB
from pathlib import Path
import json
assert json.loads(memory_tool(action='add', target='user', content='Synthetic restored preference',
                              store=load_on_disk_store()))['success']
session=SessionDB()
session.create_session('restored-session',source='api_server')
session.append_message('restored-session','user','Synthetic session')
session.close()
learned=Path('/home/hermes/skills/learned-fixture')
learned.mkdir()
(learned/'SKILL.md').write_text('Synthetic learned procedure')
(learned/'procedure.sh').write_text('#!/bin/sh\nexit 0\n')
(learned/'procedure.sh').chmod(0o700)
"""
READ = r"""
from tools.memory_tool import load_on_disk_store
from hermes_state import SessionDB
from pathlib import Path
from cron.jobs import list_jobs
assert load_on_disk_store().user_entries==['Synthetic restored preference']
session=SessionDB()
assert [m['content'] for m in session.get_messages('restored-session')]==['Synthetic session']
session.close()
learned = Path('/home/hermes/skills/learned-fixture/SKILL.md')
assert learned.read_text()=='Synthetic learned procedure'
assert learned.with_name('procedure.sh').stat().st_mode & 0o100
jobs=[j for j in list_jobs(include_disabled=True) if j.get('name')=='mental-loader-reminders']
assert len(jobs)==1 and jobs[0]['no_agent']
"""


def main():
    containers = []
    with tempfile.TemporaryDirectory(prefix="ml-gateway-check-") as temporary:
        root = Path(temporary)
        household = str(uuid4())
        try:
            manifest_path = root / "runtime.json"
            original = provision(household, None, "https://backend.invalid", manifest_path)
            containers.append(original)
            healthy = False
            for _ in range(40):
                try:
                    if httpx.get(original["endpoint"] + "/health", timeout=1).status_code == 200:
                        healthy = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            if not healthy:
                raise RuntimeError("Temporary restricted gateway did not become healthy")
            denied = httpx.post(
                original["endpoint"] + "/v1/responses",
                json={"input": "Synthetic unauthenticated request"},
                timeout=5,
            )
            assert denied.status_code in (401, 403), "Gateway did not enforce its API credential"
            subprocess.run(
                ["docker", "exec", original["container_id"], "python", "-c", WRITE],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["docker", "stop", original["container_id"]], capture_output=True, check=True
            )
            exported = export_runtime_home(manifest_path, root / "exported-home")
            try:
                provision(
                    str(uuid4()),
                    None,
                    "https://backend.invalid",
                    root / "wrong-scope.json",
                    restore_home=exported,
                    test_command=["sleep", "infinity"],
                )
                raise AssertionError("Wrong-scope native restore was accepted")
            except ValueError as error:
                assert "different runtime scope" in str(error)
            restored = provision(
                household,
                None,
                "https://backend.invalid",
                root / "restored.json",
                restore_home=exported,
                test_command=["sleep", "infinity"],
            )
            containers.append(restored)
            assert restored["api_key"] != original["api_key"]
            assert restored["tool_code"] != original["tool_code"]
            assert restored["volume"] != original["volume"]
            subprocess.run(
                ["docker", "exec", restored["container_id"], "python", "-c", READ],
                capture_output=True,
                check=True,
            )
        finally:
            for item in containers:
                subprocess.run(
                    ["docker", "rm", "-f", item["container_id"]], capture_output=True, check=True
                )
                subprocess.run(
                    ["docker", "volume", "rm", item["volume"]], capture_output=True, check=True
                )
    print(
        "PASS: actual restricted Hermes gateway health; one native no-agent cron; stopped-home "
        "export and fresh-volume restore preserved memory, sessions and learned skills while "
        "rotating runtime/tool credentials. No model calls."
    )


if __name__ == "__main__":
    main()
