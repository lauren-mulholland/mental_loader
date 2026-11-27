"""Route selection and scope capabilities, with explicitly mocked provider transport."""

import base64
import json
import os
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from conftest import login
from test_accounts import account
from test_conversations import private_chat, send
from test_feasibility import submit

from mental_loader import runtimes
from mental_loader.agents import build_agent
from mental_loader.worker import process_run


def test_container_attestation_rejects_unsafe_launch_settings(monkeypatch):
    container = "a" * 64
    good = {
        "Id": container,
        "State": {"Running": True},
        "Config": {"User": "65532:65532", "Labels": {"mental-loader.scope": "household:fixture"}},
        "HostConfig": {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "PidsLimit": 128,
            "Memory": 805306368,
            "NanoCpus": 1000000000,
        },
        "Mounts": [{"Destination": "/home/hermes", "Type": "volume", "RW": True, "Name": "own"}],
        "NetworkSettings": {"Ports": {"8642/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18642"}]}},
    }
    current = deepcopy(good)
    volume_scope = "household:fixture"
    monkeypatch.setattr(
        runtimes.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            stdout=json.dumps(
                [{"Labels": {"mental-loader.scope": volume_scope}}]
                if command[1] == "volume"
                else [current]
            )
        ),
    )
    assert runtimes.inspect_sandbox(container, "household:fixture", "http://127.0.0.1:18642")
    mutations = [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "PidsLimit", 0),
        ("HostConfig", "SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
        ("Config", "User", "0"),
        ("State", "Running", False),
    ]
    for section, key, value in mutations:
        current = deepcopy(good)
        current[section][key] = value
        with pytest.raises(ValueError, match="sandbox policy"):
            runtimes.inspect_sandbox(container, "household:fixture", "http://127.0.0.1:18642")
    current = deepcopy(good)
    current["Mounts"].append({"Destination": "/var/run/docker.sock", "Type": "bind"})
    with pytest.raises(ValueError):
        runtimes.inspect_sandbox(container, "household:fixture", "http://127.0.0.1:18642")
    current = deepcopy(good)
    volume_scope = "household:other"
    with pytest.raises(ValueError, match="state volume"):
        runtimes.inspect_sandbox(container, "household:fixture", "http://127.0.0.1:18642")


def test_distinct_scopes_use_distinct_model_routes_and_never_fallback(
    context, tmp_path, monkeypatch
):
    app, client, households, settings = context
    key = tmp_path / "key"
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    key.chmod(0o600)
    settings.secret_key_path = key
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    settings.hermes_api_key = "legacy-must-not-be-used"
    _, a = account(app, households[0][0])
    _, b = account(app, households[1][0])
    login(client, a)
    private = private_chat(client)
    monkeypatch.setattr(
        runtimes,
        "inspect_sandbox",
        lambda container, *args: {
            "Mounts": [{"Name": container}],
        },
    )
    scopes = [(households[0][0], None), (households[1][0], None), (households[0][0], private)]
    for index, (household, chat) in enumerate(scopes):
        runtimes.register(
            app.state.sessions,
            settings,
            household,
            chat,
            str(index) * 64,
            f"http://127.0.0.1:{18642 + index}",
            f"fixture-key-{index}",
            "t" * 42 + str(index),
        )
    seen = []
    real_client = httpx.Client

    def handler(request):
        index = request.url.port - 18642
        assert request.headers["authorization"] == f"Bearer fixture-key-{index}"
        seen.append((index, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": f"Fixture response from scope {index}"},
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "mental_loader.agents.httpx.Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    submit(client, "Household A fixture")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    login(client, b)
    submit(client, "Household B fixture")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    login(client, a)
    send(client, private, "Private fixture")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    assert [index for index, _ in seen] == [0, 1, 2]
    assert [payload["input"] for _, payload in seen] == [
        "Household A fixture",
        "Household B fixture",
        "Private fixture",
    ]
    assert len({payload["conversation"] for _, payload in seen}) == 3
    # A misconfigured/down registered runtime fails instead of leaking into legacy Hermes.
    monkeypatch.setattr(
        runtimes,
        "inspect_sandbox",
        lambda *args: (_ for _ in ()).throw(ValueError("Sandbox no longer valid")),
    )
    send(client, private, "Do not reroute this private message")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    assert len(seen) == 3
