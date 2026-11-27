"""Concurrent worker/transport checks run unchanged on SQLite and PostgreSQL."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from conftest import login
from sqlalchemy import select
from test_accounts import account, subscription
from test_feasibility import submit

from mental_loader.agents import build_agent
from mental_loader.storage import AIUsage, Delivery, Run, Task, now
from mental_loader.worker import deliver_pending, enqueue_due, process_run, recover_runs


def test_live_worker_is_not_interrupted_and_same_chat_is_serialized(context):
    app, client, households, _ = context
    login(client, households[0][1])
    chat, first = submit(client, "first")
    submit(client, "second", conversation=chat)
    other, third = submit(client, "independent")
    started, release = threading.Event(), threading.Event()
    seen = []

    class Agent:
        def invoke(self, state):
            seen.append(state["text"])
            if state["text"] == "first":
                started.set()
                assert release.wait(10)
            return {"reply": "Fixture completed"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(process_run, app.state.sessions, Agent())
        assert started.wait(5)
        try:
            recover_runs(app.state.sessions)
            with app.state.sessions() as db:
                assert db.get(Run, first.json()["run_id"]).status == "running"
            process_run(app.state.sessions, Agent())
            assert seen == ["first", "independent"]
            process_run(app.state.sessions, Agent())
            assert seen == ["first", "independent"]
        finally:
            release.set()
        future.result(timeout=10)
    process_run(app.state.sessions, Agent())
    assert seen == ["first", "independent", "second"]


def test_expired_worker_completion_is_fenced_and_never_replayed(context):
    app, client, households, _ = context
    login(client, households[0][1])
    _, result = submit(client, "uncertain")
    started, release = threading.Event(), threading.Event()

    class Agent:
        def invoke(self, state):
            started.set()
            assert release.wait(10)
            return {"reply": "Stale result must not overwrite interruption"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_run, app.state.sessions, Agent())
        assert started.wait(5)
        try:
            with app.state.sessions.begin() as db:
                db.get(Run, result.json()["run_id"]).lease_until = now() - timedelta(seconds=1)
            recover_runs(app.state.sessions)
        finally:
            release.set()
        future.result(timeout=10)
    with app.state.sessions() as db:
        row = db.get(Run, result.json()["run_id"])
        assert row.status == "interrupted"
        assert "Stale result" not in row.result


def test_parallel_workers_reserve_household_model_budget_atomically(context):
    app, client, households, settings = context
    user_id, code = account(app, households[0][0])
    login(client, code)
    submit(client, "fixture one")
    submit(client, "fixture two")
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    settings.ai_daily_call_limit = 1
    calls = []
    agent = build_agent(
        app.state.sessions,
        settings,
        respond=lambda text: calls.append(text) or "Fixture model response",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(process_run, app.state.sessions, agent) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert len(calls) == 1
    with app.state.sessions() as db:
        usage = list(db.scalars(select(AIUsage)))
        assert len(usage) == 1 and usage[0].user_id == user_id and usage[0].run_id
    assert client.get("/api/usage").json()["requests"] == 1


def test_concurrent_due_dispatch_and_push_claims_do_not_double_send(context):
    app, client, households, settings = context
    settings.vapid_public_key = "fixture"
    _, code = account(app, households[0][0])
    login(client, code)
    client.post("/api/push-subscriptions", json=subscription("lease"))
    submit(client)
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions.begin() as db:
        db.scalar(select(Task)).due_at = now() - timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(enqueue_due, app.state.sessions) for _ in range(3)]
        for future in futures:
            future.result(timeout=10)
    with app.state.sessions() as db:
        assert len(list(db.scalars(select(Delivery)))) == 1
    started, release = threading.Event(), threading.Event()
    sends = []

    def sender(*args):
        sends.append("sent")
        started.set()
        assert release.wait(10)
        return "sent"

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(deliver_pending, app.state.sessions, settings, sender)
        assert started.wait(5)
        try:
            deliver_pending(app.state.sessions, settings, sender)
            assert sends == ["sent"]
        finally:
            release.set()
        future.result(timeout=10)
    with app.state.sessions() as db:
        delivery = db.scalar(select(Delivery))
        assert delivery.status == "sent" and delivery.attempts == 1
