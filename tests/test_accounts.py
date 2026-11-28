"""Account authorization and migration regressions; no provider or real push calls."""

import base64
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from mental_loader import migrations
from mental_loader.accounts import provision_account
from mental_loader.migrations import v001_baseline
from mental_loader.storage import (
    Conversation,
    Delivery,
    Household,
    LoginSession,
    Membership,
    PushSubscription,
    Task,
    database,
    digest,
    now,
)
from mental_loader.worker import deliver_pending, enqueue_due, process_run


def account(app, household_id, name="Owner", role="owner"):
    with app.state.sessions.begin() as db:
        user, code = provision_account(db, household_id, name, role)
        return user.id, code


def subscription(suffix):
    return {
        "endpoint": f"https://fcm.googleapis.com/account-{suffix}",
        "keys": {
            "p256dh": base64.urlsafe_b64encode(b"\x04" + b"x" * 64).decode().rstrip("="),
            "auth": base64.urlsafe_b64encode(b"x" * 16).decode().rstrip("="),
        },
    }


def reminder(app, client):
    conversation = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{conversation}/messages",
        json={
            "content": "/remind 1m Account reminder",
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 202
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions.begin() as db:
        task = db.scalar(select(Task).where(Task.conversation_id == conversation))
        task.due_at = now() - timedelta(minutes=1)
        return conversation, task.id


def test_phase1_upgrade_preserves_credentials_sessions_and_history(database_url):
    url = database_url
    engine = create_engine(url)
    code, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with engine.begin() as connection:
        v001_baseline.upgrade(connection)
    with Session(engine) as db:
        household = v001_baseline.Household(name="Existing", access_hash=digest(code))
        db.add(household)
        db.flush()
        household_id = household.id
        chat = v001_baseline.Conversation(household_id=household_id)
        db.add(chat)
        db.flush()
        chat_id = chat.id
        db.add(
            v001_baseline.Message(
                household_id=household_id,
                conversation_id=chat_id,
                role="user",
                content="Keep me",
            )
        )
        db.add(
            v001_baseline.LoginSession(
                token_hash=digest(token),
                household_id=household_id,
                expires_at=now() + timedelta(days=1),
            )
        )
        db.commit()
    engine.dispose()
    engine, sessions = database(url)
    # Starting twice must not reapply ALTERs or reset any data.
    migrations.upgrade(engine)
    with sessions() as db:
        assert db.get(Household, household_id).access_hash == digest(code)
        assert db.get(LoginSession, digest(token)).user_id is None
        assert db.get(Conversation, chat_id).visibility == "household"
        assert db.execute(text("SELECT content FROM messages")).scalar_one() == "Keep me"
        assert list(db.scalars(text("SELECT version FROM schema_revisions ORDER BY version"))) == [
            "001",
            "002",
            "003",
            "004",
            "005",
            "006",
            "007",
            "008",
        ]
    engine.dispose()


def test_migrations_fail_closed_and_roll_back(database_url, monkeypatch):
    engine, _ = database(database_url)

    def broken(connection):
        connection.execute(text("CREATE TABLE incomplete (id INTEGER)"))
        raise RuntimeError("Simulated migration failure")

    monkeypatch.setattr(migrations, "REVISIONS", (*migrations.REVISIONS, ("009", broken)))
    with pytest.raises(RuntimeError, match="Simulated"):
        migrations.upgrade(engine)
    assert "incomplete" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO schema_revisions VALUES ('999')"))
    with pytest.raises(RuntimeError, match="Unknown or out-of-order"):
        migrations.upgrade(engine)
    engine.dispose()


def test_enrollment_disables_legacy_login_and_existing_cookie(context):
    app, client, households, _ = context
    login(client, households[0][1])
    chat = client.post("/api/conversations").json()["id"]
    user_id, code = account(app, households[0][0])
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"access_code": households[0][1]}).status_code == 401
    login(client, code)
    me = client.get("/api/me").json()
    assert (me["user_id"], me["role"], me["conversation_visibility"]) == (
        user_id,
        "owner",
        "household",
    )
    assert client.get(f"/api/conversations/{chat}/messages").status_code == 200
    # Browser enrollment never rotates/breaks the existing Hermes credential.
    assert (
        client.get(
            "/api/tool/clock",
            headers={
                "Authorization": f"Bearer {households[0][1]}",
            },
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/tool/clock",
            headers={
                "Authorization": f"Bearer {code}",
            },
        ).status_code
        == 401
    )


def test_personal_accounts_cannot_cross_household_or_runtime_boundary(context):
    app, client, households, settings = context
    _, code_a = account(app, households[0][0])
    _, code_b = account(app, households[1][0])
    login(client, code_a)
    conversation, task_id = reminder(app, client)
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    login(client, code_b)
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/conversations/{conversation}/messages").status_code == 404
    assert client.post(f"/api/tasks/{task_id}/complete").status_code == 404
    assert (
        client.post(
            f"/api/conversations/{conversation}/messages",
            json={
                "content": "Try guessed chat",
                "request_id": str(uuid4()),
            },
        ).status_code
        == 404
    )
    own_chat = client.post("/api/conversations").json()["id"]
    client.post(
        f"/api/conversations/{own_chat}/messages",
        json={
            "content": "Tell me the other household's preferences",
            "request_id": str(uuid4()),
        },
    )
    # A second household is rejected before any model transport is called.
    process_run(app.state.sessions, app.state.agent)
    messages = client.get(f"/api/conversations/{own_chat}/messages").json()["messages"]
    assert "no isolated Hermes runtime" in messages[-1]["content"]
    assert (
        client.post(
            "/api/login",
            json={
                "access_code": code_b,
                "household_id": households[0][0],
            },
        ).status_code
        == 401
    )


def test_owner_permissions_and_revocation(context):
    app, client, households, _ = context
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    _, other_code = account(app, households[1][0])
    login(client, member_code)
    assert client.get("/api/members").status_code == 403
    assert client.delete(f"/api/members/{owner_id}").status_code == 403
    with TestClient(app) as owner:
        owner.headers["origin"] = "http://localhost:8000"
        login(owner, other_code)
        assert owner.delete(f"/api/members/{member_id}").status_code == 404
        login(owner, owner_code)
        assert len(owner.get("/api/members").json()) == 2
        assert owner.delete(f"/api/members/{owner_id}").status_code == 409
        assert owner.delete(f"/api/members/{member_id}").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"access_code": member_code}).status_code == 401
    assert client.post("/api/login", json={"access_code": households[0][1]}).status_code == 401


def test_first_account_must_be_owner_and_legacy_can_be_disabled(context):
    app, client, households, settings = context
    with pytest.raises(ValueError, match="first account"):
        account(app, households[0][0], "Member", "member")
    settings.allow_legacy_login = False
    assert client.post("/api/login", json={"access_code": households[0][1]}).status_code == 401
    _, code = account(app, households[0][0])
    login(client, code)
    assert client.get("/api/me").status_code == 200


def test_multihousehold_account_requires_explicit_membership(context):
    app, client, households, _ = context
    user_id, code = account(app, households[0][0])
    account(app, households[1][0])
    with app.state.sessions.begin() as db:
        db.add(Membership(household_id=households[1][0], user_id=user_id, role="member"))
    assert client.post("/api/login", json={"access_code": code}).status_code == 409
    assert (
        client.post(
            "/api/login",
            json={
                "access_code": code,
                "household_id": households[1][0],
            },
        ).status_code
        == 200
    )
    assert client.get("/api/me").json()["role"] == "member"


def test_push_ownership_and_revoked_recipient_are_checked_at_dispatch(context):
    app, client, households, settings = context
    settings.vapid_public_key = "fixture"
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    login(client, member_code)
    body = subscription("member")
    assert client.post("/api/push-subscriptions", json=body).status_code == 200
    with TestClient(app) as owner:
        owner.headers["origin"] = "http://localhost:8000"
        login(owner, owner_code)
        assert owner.post("/api/push-subscriptions", json=body).status_code == 409
        response = owner.request("DELETE", "/api/push-subscriptions", json=body)
        assert response.status_code == 200
        with app.state.sessions() as db:
            assert db.scalar(select(PushSubscription)).user_id == member_id
        reminder(app, owner)
        enqueue_due(app.state.sessions)
        with app.state.sessions() as db:
            assert db.scalar(select(Delivery)).status == "pending"
        owner.delete(f"/api/members/{member_id}")

        def forbidden(*args):
            pytest.fail("Revoked member must not receive queued notifications")

        deliver_pending(app.state.sessions, settings, sender=forbidden)
        with app.state.sessions() as db:
            assert db.scalar(select(Delivery)).status == "cancelled"
        # A later reminder does not enqueue to that stale subscription at all.
        reminder(app, owner)
        enqueue_due(app.state.sessions)
        with app.state.sessions() as db:
            assert len(list(db.scalars(select(Delivery)))) == 1


def test_enrollment_blocks_legacy_push_until_device_is_reauthenticated(context):
    app, client, households, settings = context
    settings.vapid_public_key = "fixture"
    login(client, households[0][1])
    body = subscription("legacy")
    assert client.post("/api/push-subscriptions", json=body).status_code == 200
    _, code = account(app, households[0][0])
    login(client, code)
    reminder(app, client)
    enqueue_due(app.state.sessions)
    with app.state.sessions() as db:
        assert list(db.scalars(select(Delivery))) == []
    assert client.post("/api/push-subscriptions", json=body).status_code == 200
    reminder(app, client)
    enqueue_due(app.state.sessions)
    with app.state.sessions() as db:
        assert len(list(db.scalars(select(Delivery)))) == 1


def test_account_cli_writes_private_code_and_refuses_overwrite(context, tmp_path, monkeypatch):
    import json
    import stat
    import sys

    from mental_loader import cli
    from mental_loader.storage import User

    app, _, households, settings = context
    output = tmp_path / "credentials.json"
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mental-loader",
            "create-account",
            "--household-id",
            households[0][0],
            "--name",
            "Test owner",
            "--role",
            "owner",
            "--output",
            str(output),
        ],
    )
    cli.main()
    credentials = json.loads(output.read_text())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with app.state.sessions() as db:
        user = db.get(User, credentials["user_id"])
        assert user.access_hash == digest(credentials["access_code"])
        assert user.access_hash != credentials["access_code"]
    original = output.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main()
    assert output.read_bytes() == original
    with app.state.sessions() as db:
        assert len(list(db.scalars(select(User)))) == 1


def test_concurrent_migration_starts_are_serialized(database_url):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(3)

    def start():
        barrier.wait(timeout=10)
        engine, sessions = database(database_url)
        try:
            with sessions() as db:
                return list(
                    db.scalars(text("SELECT version FROM schema_revisions ORDER BY version"))
                )
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: start(), range(3)))
    assert results == [["001", "002", "003", "004", "005", "006", "007", "008"]] * 3
