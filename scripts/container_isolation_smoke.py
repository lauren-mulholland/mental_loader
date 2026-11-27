"""Adversarial native Hermes storage/credential checks in disposable restricted containers."""

import base64
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from runtime_box import provision

from mental_loader.accounts import provision_account
from mental_loader.app import create_app
from mental_loader.config import Settings
from mental_loader.runtimes import register
from mental_loader.storage import Household, digest

NATIVE = r"""
import json, os
from pathlib import Path
from tools.memory_tool import memory_tool, load_on_disk_store
from hermes_state import SessionDB
marker, mode = os.environ['ML_MARKER'], os.environ['ML_MODE']
assert os.getuid() == 65532
assert not Path('/var/run/docker.sock').exists()
assert not Path('/home/other-household').exists()
try:
    Path('/opt/hermes/cross-tenant').write_text('forbidden')
    raise AssertionError('Runtime source is writable')
except OSError:
    pass
store = load_on_disk_store()
session = SessionDB()
if mode == 'write':
    assert not store.user_entries
    assert session.get_session('guessed-identical-id') is None
    result = memory_tool(action='add', target='user', content=marker, store=store)
    assert json.loads(result)['success']
    session.create_session('guessed-identical-id', source='api_server')
    session.append_message('guessed-identical-id', 'user', marker)
else:
    assert store.user_entries == [marker]
    assert [m['content'] for m in session.get_messages('guessed-identical-id')] == [marker]
session.close()
print('PASS')
"""


def main():
    containers = []
    with tempfile.TemporaryDirectory(prefix="ml-container-isolation-") as temporary:
        root = Path(temporary)
        key = root / "key"
        key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
        key.chmod(0o600)
        app = create_app(
            Settings(
                _env_file=None,
                database_url=f"sqlite:///{root}/app.db",
                secret_key_path=key,
                ai_enabled=False,
            ),
            start_worker=False,
        )
        settings = Settings(
            _env_file=None, database_url=f"sqlite:///{root}/app.db", secret_key_path=key
        )
        codes, households = [], []
        with app.state.sessions.begin() as db:
            for index in range(2):
                hh = Household(name=f"Synthetic {index}", access_hash=digest(str(uuid4())))
                db.add(hh)
                db.flush()
                _, code = provision_account(db, hh.id, f"Account {index}", "owner")
                codes.append(code)
                households.append(hh.id)
        try:
            with TestClient(app) as client:
                client.headers["origin"] = "http://localhost:8000"
                client.post("/api/login", json={"access_code": codes[0]})
                private = client.post("/api/conversations", json={"visibility": "private"}).json()[
                    "id"
                ]
                for index, (hh, chat) in enumerate(
                    [
                        (households[0], None),
                        (households[1], None),
                        (households[0], private),
                    ]
                ):
                    manifest = provision(
                        hh,
                        chat,
                        "https://backend.invalid",
                        root / f"{index}.json",
                        test_command=["sleep", "infinity"],
                    )
                    containers.append(manifest)
                    register(
                        app.state.sessions,
                        settings,
                        **{
                            key: manifest[key]
                            for key in (
                                "household_id",
                                "conversation_id",
                                "container_id",
                                "endpoint",
                                "api_key",
                                "tool_code",
                            )
                        },
                    )
                    for mode in ("write", "read"):
                        result = subprocess.check_output(
                            [
                                "docker",
                                "exec",
                                "-e",
                                f"ML_MARKER=Synthetic private scope {index}",
                                "-e",
                                f"ML_MODE={mode}",
                                manifest["container_id"],
                                "python",
                                "-c",
                                NATIVE,
                            ],
                            text=True,
                        )
                        assert result.strip().endswith("PASS")
                    # The credential cannot become a browser account.
                    assert (
                        client.post(
                            "/api/login",
                            json={
                                "access_code": manifest["tool_code"],
                            },
                        ).status_code
                        == 401
                    )
                private_headers = {"authorization": f"Bearer {containers[2]['tool_code']}"}
                args = {
                    "title": "Private synthetic tool task",
                    "due_at": "2099-01-01T00:00:00Z",
                    "request_id": str(uuid4()),
                }
                assert (
                    client.post(
                        "/api/tool/reminders", headers=private_headers, json=args
                    ).status_code
                    == 200
                )
                for manifest in containers[:2]:
                    headers = {"authorization": f"Bearer {manifest['tool_code']}"}
                    assert client.get("/api/tool/tasks", headers=headers).json() == []
                    assert (
                        client.post(
                            "/api/tool/reminders",
                            headers=headers,
                            json=dict(
                                args,
                                request_id=str(uuid4()),
                                conversation_id=private,
                            ),
                        ).status_code
                        == 404
                    )
                assert (
                    client.post("/api/tool/dispatch-reminders", headers=private_headers).status_code
                    == 403
                )
                rows = client.get("/api/tool/tasks", headers=private_headers).json()
                assert [r["title"] for r in rows] == ["Private synthetic tool task"]
                # Revocation removes the tool capability immediately.
                from sqlalchemy import delete

                from mental_loader.storage import RuntimeBinding

                with app.state.sessions.begin() as db:
                    db.execute(
                        delete(RuntimeBinding).where(RuntimeBinding.conversation_id == private)
                    )
                assert client.get("/api/tool/tasks", headers=private_headers).status_code == 401
        finally:
            for manifest in containers:
                subprocess.run(
                    ["docker", "rm", "-f", manifest["container_id"]],
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["docker", "volume", "rm", manifest["volume"]], capture_output=True, check=True
                )
    print(
        "PASS: two household containers plus a private-chat container; non-root, read-only "
        "source, no host/socket mounts, separate native memory/sessions and credentials, "
        "private tool denial, guessed IDs and immediate capability revocation. No model calls."
    )


if __name__ == "__main__":
    main()
