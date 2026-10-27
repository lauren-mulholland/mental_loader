from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import login
from sqlalchemy import func, select

from mental_loader.agents import build_agent
from mental_loader.app import create_app
from mental_loader.domain import parse_reminder
from mental_loader.push import validate_endpoint
from mental_loader.storage import (
    AIUsage,
    Delivery,
    Message,
    PushSubscription,
    Run,
    Task,
    digest,
    now,
)
from mental_loader.worker import deliver_pending, enqueue_due, process_run, recover_runs


def submit(client, content="/remind 1m Buy a birthday card", request_id=None, conversation=None):
    conversation = conversation or client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{conversation}/messages",
        json={
            "content": content,
            "request_id": request_id or str(uuid4()),
        },
    )
    return conversation, response


def test_reminder_and_persistent_history(context):
    app, client, households, settings = context
    login(client, households[0][1])
    conversation, response = submit(client)
    assert response.status_code == 202
    process_run(app.state.sessions, app.state.agent)
    messages = client.get(f"/api/conversations/{conversation}/messages").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert client.get("/api/tasks").json()[0]["title"] == "Buy a birthday card"
    # New application instance reads the same on-disk database.
    restarted = create_app(settings, start_worker=False)
    with restarted.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_all_household_routes_are_scoped(context):
    app, client, households, _ = context
    login(client, households[0][1])
    conversation, _ = submit(client)
    process_run(app.state.sessions, app.state.agent)
    task_id = client.get("/api/tasks").json()[0]["id"]
    login(client, households[1][1])
    assert client.get("/api/conversations").json() == []
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/conversations/{conversation}/messages").status_code == 404
    _, result = submit(client, conversation=conversation)
    assert result.status_code == 404
    assert client.post(f"/api/tasks/{task_id}/complete").status_code == 404
    # Passing a model/browser-selected tenant is rejected, not used.
    other = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{other}/messages",
        json={
            "content": "hello",
            "request_id": str(uuid4()),
            "household_id": households[0][0],
        },
    )
    assert result.status_code == 422


def test_auth_origin_logout_and_expiry(context):
    app, client, households, _ = context
    assert client.get("/api/tasks").status_code == 401
    assert (
        client.post(
            "/api/login",
            json={"access_code": households[0][1]},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    login(client, households[0][1])
    cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "ml_session")
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/tasks").status_code == 401


def test_duplicate_submission_and_conflict(context):
    app, client, households, _ = context
    login(client, households[0][1])
    request_id = str(uuid4())
    conversation, first = submit(client, request_id=request_id)
    _, second = submit(client, request_id=request_id, conversation=conversation)
    assert first.json()["run_id"] == second.json()["run_id"]
    _, conflict = submit(
        client, content="different", request_id=request_id, conversation=conversation
    )
    assert conflict.status_code == 409
    process_run(app.state.sessions, app.state.agent)
    process_run(app.state.sessions, app.state.agent)
    assert len(client.get("/api/tasks").json()) == 1


def test_pending_runs_survive_and_running_runs_are_not_replayed(context):
    app, client, households, _ = context
    login(client, households[0][1])
    _, response = submit(client)
    recover_runs(app.state.sessions)
    with app.state.sessions() as db:
        assert db.get(Run, response.json()["run_id"]).status == "pending"
    with app.state.sessions.begin() as db:
        db.get(Run, response.json()["run_id"]).status = "running"
    recover_runs(app.state.sessions)
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions() as db:
        assert db.get(Run, response.json()["run_id"]).status == "interrupted"
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize(
    "text",
    ["/remind 0m empty", "/remind 2026-11-01T09:00 test", "/remind 2020-01-01T09:00:00+11:00 past"],
)
def test_invalid_dates_rejected(text):
    with pytest.raises(ValueError):
        parse_reminder(text, "Australia/Sydney", datetime(2026, 10, 26, tzinfo=UTC))


def test_offsets_and_daylight_saving_are_explicit():
    clock = datetime(2026, 9, 1, tzinfo=UTC)
    _, winter = parse_reminder("/remind 2026-09-02T09:00:00+10:00 test", "Australia/Sydney", clock)
    _, summer = parse_reminder("/remind 2026-12-02T09:00:00+11:00 test", "Australia/Sydney", clock)
    assert winter.hour == 23 and summer.hour == 22


def due_task(context):
    app, client, households, settings = context
    login(client, households[0][1])
    conversation, response = submit(client)
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions.begin() as db:
        task = db.scalar(select(Task))
        task.due_at = now() - timedelta(minutes=1)
        task_id = task.id
        for household, suffix in ((households[0], "a"), (households[1], "b")):
            endpoint = f"https://fcm.googleapis.com/test-{suffix}"
            db.add(
                PushSubscription(
                    household_id=household[0],
                    endpoint_hash=digest(endpoint),
                    subscription={"endpoint": endpoint, "keys": {}},
                )
            )
    return task_id


def test_due_reminders_and_push_are_scoped_and_deduplicated(context):
    task_id = due_task(context)
    app, client, households, settings = context
    enqueue_due(app.state.sessions)
    enqueue_due(app.state.sessions)
    sent = []

    def sender(subscription, task, conversation, settings):
        sent.append(subscription["endpoint"])
        return "sent"

    deliver_pending(app.state.sessions, settings, sender=sender)
    deliver_pending(app.state.sessions, settings, sender=sender)
    assert sent == ["https://fcm.googleapis.com/test-a"]
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Delivery)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.content == "Reminder: Buy a birthday card")
            )
            == 1
        )
    assert client.post(f"/api/tasks/{task_id}/complete").status_code == 200


def test_push_retry_then_expired_subscription_cleanup(context):
    due_task(context)
    app, _, _, settings = context
    clock = now()
    enqueue_due(app.state.sessions, clock)

    def fail(*args):
        raise TimeoutError()

    deliver_pending(app.state.sessions, settings, sender=fail, clock=clock)
    with app.state.sessions() as db:
        delivery = db.scalar(select(Delivery))
        assert delivery.status == "pending" and delivery.attempts == 1
    deliver_pending(
        app.state.sessions,
        settings,
        sender=lambda *args: "expired",
        clock=clock + timedelta(minutes=3),
    )
    with app.state.sessions() as db:
        assert db.scalar(select(Delivery)).status == "expired"
        assert db.scalar(select(func.count()).select_from(PushSubscription)) == 1


def test_completion_cancels_queued_push(context):
    task_id = due_task(context)
    app, client, _, settings = context
    enqueue_due(app.state.sessions)
    client.post(f"/api/tasks/{task_id}/complete")

    def forbidden(*args):
        pytest.fail("Completed task must not send")

    deliver_pending(app.state.sessions, settings, sender=forbidden)
    with app.state.sessions() as db:
        assert db.scalar(select(Delivery)).status == "cancelled"


def test_chat_disabled_does_not_spend(context):
    app, client, households, _ = context
    login(client, households[0][1])
    submit(client, "Suggest gift ideas")
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AIUsage)) == 0


def test_hermes_request_budget_with_stubbed_response(context):
    app, client, households, settings = context
    login(client, households[0][1])
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    settings.ai_daily_call_limit = 1
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return "Fixture response, not live AI"

    agent = build_agent(app.state.sessions, settings, respond=fake)
    submit(client, "Suggest gardening gifts")
    process_run(app.state.sessions, agent)
    submit(client, "Suggest another gift")
    process_run(app.state.sessions, agent)
    assert calls == ["Suggest gardening gifts"]
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(AIUsage)) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost/a",
        "https://127.0.0.1/a",
        "https://fcm.googleapis.com.evil.example/a",
        "https://fcm.googleapis.com:444/a",
        "https://user:password@fcm.googleapis.com/a",
    ],
)
def test_push_cannot_target_arbitrary_services(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


def test_pwa_assets_and_no_private_data_caching(context):
    _, client, _, _ = context
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/api/tasks").headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
