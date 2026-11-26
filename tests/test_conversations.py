"""Adversarial conversation access checks across HTTP, jobs, tools and delivery."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_accounts import account, subscription

from mental_loader.agents import build_agent
from mental_loader.storage import (
    AIUsage,
    Conversation,
    ConversationParticipant,
    Delivery,
    Run,
    Task,
    now,
)
from mental_loader.worker import deliver_pending, enqueue_due, process_run


def sign_in(client, code):
    assert client.post("/api/login", json={"access_code": code}).status_code == 200


def private_chat(client):
    response = client.post("/api/conversations", json={"visibility": "private"})
    assert response.status_code == 200
    return response.json()["id"]


def send(client, chat, text="/remind 1m Private task", request_id=None):
    return client.post(
        f"/api/conversations/{chat}/messages",
        json={
            "content": text,
            "request_id": request_id or str(uuid4()),
        },
    )


def test_same_household_owner_cannot_access_another_members_private_records(context):
    app, client, households, _ = context
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    sign_in(client, member_code)
    chat = private_chat(client)
    assert send(client, chat).status_code == 202
    process_run(app.state.sessions, app.state.agent)
    task_id = client.get("/api/tasks").json()[0]["id"]
    sign_in(client, owner_code)
    assert client.get("/api/conversations").json() == []
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/conversations/{chat}/messages").status_code == 404
    assert client.get(f"/api/conversations/{chat}/participants").status_code == 404
    assert client.get(f"/api/conversations/{chat}/eligible-participants").status_code == 404
    assert send(client, chat).status_code == 404
    assert client.post(f"/api/tasks/{task_id}/complete").status_code == 404
    assert (
        client.post(
            f"/api/conversations/{chat}/participants",
            json={
                "user_id": owner_id,
            },
        ).status_code
        == 404
    )
    sign_in(client, member_code)
    assert client.get(f"/api/conversations/{chat}/messages").json()["visibility"] == "private"
    assert client.get(f"/api/conversations/{chat}/participants").json() == [
        {"user_id": member_id, "name": "Member", "owner": True},
    ]


def test_only_creator_can_share_and_only_with_current_household_members(context):
    app, client, households, _ = context
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    outsider_id, _ = account(app, households[1][0])
    sign_in(client, member_code)
    chat = private_chat(client)
    path = f"/api/conversations/{chat}/participants"
    assert client.post(path, json={"user_id": outsider_id}).status_code == 404
    for _ in range(2):
        assert client.post(path, json={"user_id": owner_id}).status_code == 200
    assert len(client.get(path).json()) == 2
    sign_in(client, owner_code)
    assert client.get(f"/api/conversations/{chat}/messages").status_code == 200
    assert client.post(path, json={"user_id": outsider_id}).status_code == 403
    assert client.delete(f"{path}/{member_id}").status_code == 403
    sign_in(client, member_code)
    assert client.delete(f"{path}/{member_id}").status_code == 409
    assert client.delete(f"{path}/{owner_id}").status_code == 200
    sign_in(client, owner_code)
    assert client.get(f"/api/conversations/{chat}/messages").status_code == 404


def test_legacy_and_household_tools_cannot_open_or_replay_private_chat(context):
    app, client, households, _ = context
    sign_in(client, households[0][1])
    assert client.post("/api/conversations", json={"visibility": "private"}).status_code == 403
    _, code = account(app, households[0][0])
    sign_in(client, code)
    chat = private_chat(client)
    request_id = str(uuid4())
    due_at = (now() + timedelta(minutes=5)).isoformat()
    assert send(client, chat, f"/remind {due_at} Secret title", request_id).status_code == 202
    process_run(app.state.sessions, app.state.agent)
    headers = {"authorization": f"Bearer {households[0][1]}"}
    assert client.get("/api/tool/tasks", headers=headers).json() == []
    body = {
        "title": "Secret title",
        "due_at": due_at,
        "request_id": request_id,
        "conversation_id": chat,
    }
    assert client.post("/api/tool/reminders", json=body, headers=headers).status_code == 404
    body["request_id"] = str(uuid4())
    assert client.post("/api/tool/reminders", json=body, headers=headers).status_code == 404


def test_private_messages_never_reach_shared_hermes_or_usage_budget(context):
    app, client, households, settings = context
    _, code = account(app, households[0][0])
    sign_in(client, code)
    settings.ai_enabled = True
    settings.hermes_household_id = households[0][0]
    chat = private_chat(client)
    send(client, chat, "Remember a confidential preference")
    called = []
    agent = build_agent(app.state.sessions, settings, respond=lambda prompt: called.append(prompt))
    process_run(app.state.sessions, agent)
    assert called == []
    reply = client.get(f"/api/conversations/{chat}/messages").json()["messages"][-1]["content"]
    assert "separate private runtime" in reply
    with app.state.sessions() as db:
        assert list(db.scalars(select(AIUsage))) == []


def test_revoked_participant_cannot_execute_pending_work(context):
    app, client, households, _ = context
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    sign_in(client, owner_code)
    chat = private_chat(client)
    path = f"/api/conversations/{chat}/participants"
    client.post(path, json={"user_id": member_id})
    sign_in(client, member_code)
    run_id = send(client, chat).json()["run_id"]
    sign_in(client, owner_code)
    client.delete(f"{path}/{member_id}")
    process_run(app.state.sessions, app.state.agent)
    with app.state.sessions() as db:
        assert db.get(Run, run_id).status == "cancelled"
        assert list(db.scalars(select(Task))) == []
    # Membership removal also removes conversation participation at the DB boundary.
    client.post(path, json={"user_id": member_id})
    assert client.delete(f"/api/members/{member_id}").status_code == 200
    with app.state.sessions() as db:
        assert db.get(ConversationParticipant, (chat, member_id)) is None


def test_private_push_rechecks_participants_after_enqueue(context):
    app, client, households, settings = context
    settings.vapid_public_key = "fixture"
    owner_id, owner_code = account(app, households[0][0])
    member_id, member_code = account(app, households[0][0], "Member", "member")
    sign_in(client, member_code)
    client.post("/api/push-subscriptions", json=subscription("participant"))
    with TestClient(app) as owner:
        owner.headers["origin"] = "http://localhost:8000"
        sign_in(owner, owner_code)
        owner.post("/api/push-subscriptions", json=subscription("creator"))
        chat = private_chat(owner)
        path = f"/api/conversations/{chat}/participants"
        owner.post(path, json={"user_id": member_id})
        send(owner, chat)
        process_run(app.state.sessions, app.state.agent)
        with app.state.sessions.begin() as db:
            db.scalar(select(Task)).due_at = now() - timedelta(minutes=1)
        enqueue_due(app.state.sessions)
        with app.state.sessions() as db:
            assert len(list(db.scalars(select(Delivery)))) == 2
        owner.delete(f"{path}/{member_id}")
        sent = []
        deliver_pending(
            app.state.sessions,
            settings,
            sender=lambda sub, *args: sent.append(sub["endpoint"]) or "sent",
        )
        assert sent == ["https://fcm.googleapis.com/account-creator"]
        with app.state.sessions() as db:
            assert sorted(db.scalars(select(Delivery.status))) == ["cancelled", "sent"]


def test_participant_database_constraints_reject_cross_household_members(context):
    app, client, households, _ = context
    _, code = account(app, households[0][0])
    outsider_id, _ = account(app, households[1][0])
    sign_in(client, code)
    chat = private_chat(client)
    for household_id in (households[0][0], households[1][0]):
        with pytest.raises(IntegrityError), app.state.sessions.begin() as db:
            db.add(
                ConversationParticipant(
                    conversation_id=chat,
                    household_id=household_id,
                    user_id=outsider_id,
                )
            )
    with pytest.raises(IntegrityError), app.state.sessions.begin() as db:
        db.add(Conversation(household_id=households[0][0], visibility="private"))
