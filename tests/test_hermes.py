import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from conftest import login
from sqlalchemy import func, select
from test_feasibility import submit

from mental_loader.agents import build_agent
from mental_loader.storage import AIUsage, Run, Task, now
from mental_loader.worker import process_run


def test_native_tool_auth_idempotency_and_isolation(context):
    app, client, households, _ = context
    login(client, households[0][1])
    conversation = client.post("/api/conversations").json()["id"]
    payload = {
        "title": "Native Hermes tool",
        "due_at": (now() + timedelta(hours=1)).isoformat(),
        "request_id": str(uuid4()),
        "conversation_id": conversation,
    }
    assert client.post("/api/tool/reminders", json=payload).status_code == 401
    headers = {"authorization": f"Bearer {households[0][1]}"}
    first = client.post("/api/tool/reminders", json=payload, headers=headers)
    second = client.post("/api/tool/reminders", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    other = {"authorization": f"Bearer {households[1][1]}"}
    payload["request_id"] = str(uuid4())
    assert client.post("/api/tool/reminders", json=payload, headers=other).status_code == 404
    assert client.get("/api/tool/tasks", headers=other).json() == []
    assert client.get("/api/tool/clock", headers=headers).json()["timezone"] == "Australia/Sydney"
    payload["household_id"] = households[0][0]
    assert client.post("/api/tool/reminders", json=payload, headers=other).status_code == 422
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_hermes_adapter_uses_stable_conversation_and_no_auto_retries(context, monkeypatch):
    app, client, households, settings = context
    login(client, households[0][1])
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    settings.hermes_api_key = "fixture-key"
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/responses"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 15},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Fixture Hermes response"}],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "mental_loader.agents.httpx.Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    conversation, _ = submit(client, "Hello Hermes")
    agent = build_agent(app.state.sessions, settings)
    process_run(app.state.sessions, agent)
    submit(client, "Continue", conversation=conversation)
    process_run(app.state.sessions, agent)
    payloads = [json.loads(request.content) for request in requests]
    assert payloads[0]["conversation"] == payloads[1]["conversation"]
    assert requests[0].headers["idempotency-key"] != requests[1].headers["idempotency-key"]
    assert requests[0].headers["authorization"] == "Bearer fixture-key"
    with app.state.sessions() as db:
        assert db.scalar(select(AIUsage)).input_tokens == 15


def test_second_household_cannot_use_personal_hermes(context):
    app, client, households, settings = context
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    login(client, households[1][1])
    submit(client, "Hello")

    def forbidden(prompt):
        raise AssertionError("Must not call another household's Hermes")

    process_run(app.state.sessions, build_agent(app.state.sessions, settings, forbidden))
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AIUsage)) == 0


def test_http_200_with_failed_turn_is_logged_safely_without_replay(context, monkeypatch, caplog):
    app, client, households, settings = context
    login(client, households[0][1])
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    settings.hermes_api_key = "fixture-key"
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "status": "failed",
            "error": {"message": "invalid_encrypted_content secret-provider-diagnostic"},
            "usage": {"input_tokens": 19},
        })

    monkeypatch.setattr(
        "mental_loader.agents.httpx.Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    _, response = submit(client, "fixture-private-household-message")
    run_id = response.json()["run_id"]
    process_run(app.state.sessions, app.state.agent)
    assert len(requests) == 1
    with app.state.sessions() as db:
        run = db.get(Run, run_id)
        assert run.status == "failed"
        assert run_id in run.result
        assert db.scalar(select(AIUsage)).input_tokens == 19
    assert run_id in caplog.text
    assert "invalid_encrypted_content" in caplog.text
    assert "secret-provider-diagnostic" not in caplog.text
    assert "fixture-private-household-message" not in caplog.text


def test_native_plugin_registration_uses_real_handler_shape(monkeypatch):
    path = Path(__file__).parents[1] / "hermes_plugin/__init__.py"
    spec = importlib.util.spec_from_file_location("mental_loader_plugin", path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setenv("ML_HOUSEHOLD_ACCESS_CODE", "a" * 43)

    class Context:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **kwargs):
            self.tools[kwargs["name"]] = kwargs

    ctx = Context()
    plugin.register(ctx)
    assert set(ctx.tools) == {"household_clock", "household_tasks", "household_remind"}
    assert "household_id" not in ctx.tools["household_remind"]["schema"]["parameters"]["properties"]


def test_native_dispatch_is_authenticated_and_household_scoped(context):
    app, client, households, _ = context
    for household_id, code in households:
        login(client, code)
        submit(client, "/remind 1m Dispatch scope check")
        process_run(app.state.sessions, app.state.agent)
    with app.state.sessions.begin() as db:
        for task in db.scalars(select(Task)):
            task.due_at = now() - timedelta(minutes=1)
    assert client.post("/api/tool/dispatch-reminders").status_code == 401
    response = client.post(
        "/api/tool/dispatch-reminders",
        headers={
            "authorization": f"Bearer {households[0][1]}",
        },
    )
    assert response.status_code == 200
    with app.state.sessions() as db:
        tasks = {task.household_id: task for task in db.scalars(select(Task))}
        assert tasks[households[0][0]].notified_at is not None
        assert tasks[households[1][0]].notified_at is None
