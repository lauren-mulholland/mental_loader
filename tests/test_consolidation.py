"""Cross-feature boundaries for birthdays and the explicitly personal PaaS route."""

import base64
import json
import os
from uuid import uuid4

import httpx
import pytest
from conftest import login
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from test_accounts import account
from test_conversations import private_chat, send
from test_feasibility import submit

from mental_loader.agents import build_agent
from mental_loader.storage import Birthday, Task
from mental_loader.worker import process_run


def test_birthdays_follow_private_chat_permissions_and_cannot_be_reparented(context):
    app, client, households, settings = context
    _, owner = account(app, households[0][0])
    _, member = account(app, households[0][0], name="Member", role="member")
    login(client, owner)
    private = private_chat(client)
    shared = client.post("/api/conversations").json()["id"]
    body = {"conversation_id": private, "recipient": "Private recipient",
            "occasion_date": "2027-02-20"}
    path = f"/api/birthdays/{uuid4()}"
    saved = client.put(path, json=body)
    assert saved.status_code == 200
    task_id = saved.json()["task_id"]
    assert client.put(path, json={**body, "conversation_id": shared}).status_code == 422
    login(client, member)
    assert client.get("/api/birthdays").json() == []
    assert client.put(path, json={**body, "conversation_id": shared}).status_code == 404
    assert client.post(f"/api/tasks/{task_id}/complete").status_code == 404
    settings.hosted_mode = settings.paas_personal_runtime = True
    settings.hermes_household_id = households[0][0]
    settings.hermes_tool_token = "t" * 43
    headers = {"authorization": "Bearer " + settings.hermes_tool_token}
    assert client.get("/api/tool/birthdays", headers=headers).json() == []
    assert client.put(path.replace("/api/", "/api/tool/"), json=body,
                      headers=headers).status_code == 404
    assert client.get("/api/tool/tasks", headers={
        "authorization": "Bearer " + households[0][1]
    }).status_code == 401
    with app.state.sessions() as db:
        assert db.get(Task, task_id).status == "open"
    with pytest.raises(IntegrityError):
        with app.state.sessions.begin() as db:
            # Even a direct DB update cannot move a record into a different chat.
            db.execute(update(Birthday).where(Birthday.id == path.rsplit("/", 1)[1]).values(
                conversation_id=shared, task_id=None,
            ))


def test_paas_shared_route_never_receives_private_or_other_household_context(context, monkeypatch):
    app, client, households, settings = context
    _, owner = account(app, households[0][0])
    _, other = account(app, households[1][0])
    settings.hosted_mode = settings.paas_personal_runtime = settings.ai_enabled = True
    settings.allow_legacy_login = False
    settings.hermes_household_id = households[0][0]
    settings.hermes_api_key = "fixture-api-key" * 3
    settings.hermes_base_url = "http://private-render-service:8642"
    settings.hermes_tool_token = "t" * 43
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.host == "private-render-service"
        assert request.headers["x-hermes-session-key"] == body["conversation"]
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": "Fixture"}],
        }]})

    monkeypatch.setattr("mental_loader.agents.httpx.Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handler)
    ))
    login(client, owner)
    for text in ("First shared conversation", "Second shared conversation"):
        submit(client, text)
        process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    private = private_chat(client)
    send(client, private, "Private content")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    login(client, other)
    submit(client, "Another household")
    process_run(app.state.sessions, build_agent(app.state.sessions, settings))
    assert len(requests) == 2
    assert len({request.headers["x-hermes-session-key"] for request in requests}) == 2


def test_personal_hosted_mode_requires_distinct_tool_configuration(context, tmp_path):
    from mental_loader.security import validate_hosted

    _, _, households, settings = context
    key = tmp_path / "app-key"
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    key.chmod(0o600)
    settings.secret_key_path = key
    settings.database_url = "postgresql+psycopg://fixture/db"
    settings.hosted_mode = settings.paas_personal_runtime = settings.cookie_secure = True
    settings.allow_legacy_login = False
    settings.public_origin = "https://fixture.onrender.com"
    with pytest.raises(ValueError, match="separate API/tool keys"):
        validate_hosted(settings)
    settings.hermes_household_id = households[0][0]
    settings.hermes_api_key = "k" * 43
    settings.hermes_tool_token = "t" * 43
    validate_hosted(settings)
