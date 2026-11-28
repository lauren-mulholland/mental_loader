"""Application + HTTP/tool regressions. No provider or runtime calls are made."""

from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import login
from sqlalchemy import func, select
from test_accounts import account
from test_conversations import private_chat

from mental_loader.accounts import Principal
from mental_loader.application import ApplicationError, HouseholdOperations
from mental_loader.birthdays import BirthdayInput
from mental_loader.storage import (
    Birthday,
    Household,
    Membership,
    Run,
    RuntimeBinding,
    Task,
    digest,
    now,
)


def test_ui_and_tool_use_same_records_validation_and_idempotency(context):
    app, client, households, _ = context
    login(client, households[0][1])
    chat = client.post("/api/conversations").json()["id"]
    headers = {"authorization": "Bearer " + households[0][1]}
    body = {"conversation_id": chat, "recipient": "Sam", "occasion_date": "2027-01-20"}
    birthday_id = str(uuid4())
    direct = f"/api/birthdays/{birthday_id}"
    tool = f"/api/tool/birthdays/{birthday_id}"
    saved = client.put(direct, json=body).json()
    for path in (direct, tool):
        assert (
            client.put(path, json={**body, "recipient": "  "}, headers=headers).status_code == 422
        )
    edited = client.put(tool, json={**body, "lead_days": 7}, headers=headers).json()
    assert edited["task_id"] == saved["task_id"]
    assert client.get("/api/birthdays").json()[0]["lead_days"] == 7
    assert (
        client.post(f"/api/tool/tasks/{saved['task_id']}/complete", headers=headers).status_code
        == 200
    )
    assert client.get("/api/birthdays").json()[0]["gift_status"] == "ready"
    reminder = {
        "conversation_id": chat,
        "title": "Buy card",
        "due_at": (now() + timedelta(days=2)).isoformat(),
        "request_id": str(uuid4()),
    }
    first = client.post("/api/reminders", json=reminder)
    replay = client.post("/api/tool/reminders", json=reminder, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json()["task_id"] == replay.json()["task_id"]
    assert (
        client.post(
            "/api/tool/reminders", json={**reminder, "title": "Changed"}, headers=headers
        ).status_code
        == 409
    )
    for path in ("/api/reminders", "/api/tool/reminders"):
        assert (
            client.post(
                path,
                json={**reminder, "request_id": str(uuid4()), "due_at": "2027-01-20T09:00:00"},
                headers=headers,
            ).status_code
            == 422
        )
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Birthday)) == 1
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.scalar(select(func.count()).select_from(Run)) == 2


def test_application_operation_enforces_scope_without_http(context):
    app, client, households, _ = context
    user_id, code = account(app, households[0][0])
    login(client, code)
    private = private_chat(client)
    shared = client.post("/api/conversations").json()["id"]
    operations = HouseholdOperations(app.state.sessions)
    with app.state.sessions() as db:
        principal = Principal(
            db.get(Household, households[0][0]), user_id, "runtime", private, True
        )
        other = Principal(db.get(Household, households[1][0]), None, "legacy")
    body = BirthdayInput(conversation_id=private, recipient="Sam", occasion_date="2027-01-20")
    birthday_id = str(uuid4())
    saved = operations.save_birthday(principal, birthday_id, body)
    for tenant, attempted in (
        (principal, body.model_copy(update={"conversation_id": shared})),
        (other, body),
    ):
        with pytest.raises(ApplicationError) as error:
            operations.save_birthday(tenant, birthday_id, attempted)
        assert error.value.status_code == 404
    with pytest.raises(ApplicationError):
        operations.complete_task(other, saved["task_id"])
    assert operations.birthdays(other) == []


def test_private_runtime_cannot_read_write_or_complete_other_chats(context):
    app, client, households, _ = context
    _, code = account(app, households[0][0])
    login(client, code)
    private = private_chat(client)
    shared = client.post("/api/conversations").json()["id"]
    birthday = {"conversation_id": shared, "recipient": "Shared", "occasion_date": "2027-01-20"}
    birthday_id = str(uuid4())
    saved = client.put(f"/api/birthdays/{birthday_id}", json=birthday).json()
    token = "scoped-runtime-fixture-" + "x" * 32
    with app.state.sessions.begin() as db:
        db.add(
            RuntimeBinding(
                scope="fixture-private",
                household_id=households[0][0],
                conversation_id=private,
                container_id="fixture-container",
                endpoint="http://fixture:8642",
                encrypted_api_key="fixture",
                tool_hash=digest(token),
            )
        )
    headers = {"authorization": "Bearer " + token}
    assert client.get("/api/tool/birthdays", headers=headers).json() == []
    assert client.get("/api/tool/tasks", headers=headers).json() == []
    assert (
        client.put(f"/api/tool/birthdays/{birthday_id}", json=birthday, headers=headers).status_code
        == 404
    )
    assert (
        client.post(f"/api/tool/tasks/{saved['task_id']}/complete", headers=headers).status_code
        == 404
    )
    reminder = {
        "conversation_id": shared,
        "title": "Cannot write shared",
        "due_at": (now() + timedelta(days=2)).isoformat(),
        "request_id": str(uuid4()),
    }
    assert client.post("/api/tool/reminders", json=reminder, headers=headers).status_code == 404
    reminder["conversation_id"] = private
    first = client.post("/api/tool/reminders", json=reminder, headers=headers)
    assert first.status_code == 200
    assert (
        client.post(
            f"/api/tool/tasks/{first.json()['task_id']}/complete", headers=headers
        ).status_code
        == 200
    )


def test_application_rechecks_membership_for_retained_principal(context):
    app, client, households, _ = context
    user_id, code = account(app, households[0][0])
    login(client, code)
    chat = client.post("/api/conversations").json()["id"]
    with app.state.sessions.begin() as db:
        principal = Principal(db.get(Household, households[0][0]), user_id, "member")
        db.delete(db.get(Membership, (households[0][0], user_id)))
    operations = HouseholdOperations(app.state.sessions)
    for operation in (operations.birthdays, operations.tasks):
        with pytest.raises(ApplicationError) as error:
            operation(principal)
        assert error.value.status_code == 401
    with pytest.raises(ApplicationError) as error:
        operations.save_birthday(
            principal,
            str(uuid4()),
            BirthdayInput(conversation_id=chat, recipient="Sam", occasion_date="2027-01-20"),
        )
    assert error.value.status_code == 401
