from datetime import UTC, date, datetime
from uuid import uuid4

from conftest import login
from sqlalchemy import func, select

from mental_loader.birthdays import import_calendar_event, preparation_deadline
from mental_loader.storage import Birthday, Delivery, Household, Run, Task
from mental_loader.worker import enqueue_due


def setup_birthday(context):
    app, client, households, _ = context
    login(client, households[0][1])
    conversation = client.post("/api/conversations").json()["id"]
    body = dict(
        conversation_id=conversation,
        recipient="Sam",
        occasion_date="2027-01-20",
        budget="40.00",
        preferences="Gardening",
        lead_days=14,
    )
    return app, client, households, body


def test_lifecycle_retry_and_isolation(context):
    app, client, households, body = setup_birthday(context)
    birthday_id = str(uuid4())
    path = f"/api/birthdays/{birthday_id}"
    first = client.put(path, json=body)
    assert first.status_code == 200
    assert client.put(path, json=body).json()["task_id"] == first.json()["task_id"]
    task_id = first.json()["task_id"]
    with app.state.sessions.begin() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert len(db.scalar(select(Run)).request_id) == 36
        db.add(Delivery(household_id=households[0][0], task_id=task_id, subscription_id="test"))
    assert client.put(path, json={**body, "gift_status": "purchased"}).status_code == 200
    assert client.get("/api/tasks").json()[0]["status"] == "open"
    assert client.put(path, json={**body, "gift_status": "ready"}).status_code == 200
    enqueue_due(app.state.sessions, datetime(2028, 1, 1, tzinfo=UTC))
    with app.state.sessions() as db:
        assert db.get(Task, task_id).status == "done"
        assert db.scalar(select(Delivery)).status == "cancelled"
        assert db.get(Task, task_id).notified_at is None
    login(client, households[1][1])
    assert client.get("/api/birthdays").json() == []
    assert client.put(path, json=body).status_code == 404
    assert client.put(f"/api/birthdays/{uuid4()}", json=body).status_code == 404


def test_calendar_update_cancel_and_replay(context):
    app, client, households, body = setup_birthday(context)
    with app.state.sessions.begin() as db:
        tenant = db.get(Household, households[0][0])
        event = {"id": "event-one", "summary": "Sam", "start": {"date": "2027-01-20"}}
        row = import_calendar_event(db, tenant, body["conversation_id"], "primary", event)
        task_id = row.task_id
        row.budget = "40.00"
        event["start"]["date"] = "2027-02-20"
        assert (
            import_calendar_event(db, tenant, body["conversation_id"], "primary", event).id
            == row.id
        )
        assert db.get(Task, task_id).due_at.day == 5  # Sydney 09:00 = previous UTC day
        assert row.budget == "40.00"
        cancelled = {"id": event["id"], "status": "cancelled"}
        import_calendar_event(db, tenant, body["conversation_id"], "primary", cancelled)
        import_calendar_event(db, tenant, body["conversation_id"], "primary", cancelled)
        assert db.get(Task, task_id).status == "done"
        assert db.scalar(select(func.count()).select_from(Birthday)) == 1
    enqueue_due(app.state.sessions, datetime(2028, 1, 1, tzinfo=UTC))
    assert client.get("/api/tasks").json()[0]["notified_at"] is None


def test_deadline_dst_and_leap_day():
    assert preparation_deadline(date(2026, 10, 5), 2, "Australia/Sydney") == datetime(
        2026, 10, 2, 23, tzinfo=UTC
    )
    assert preparation_deadline(date(2028, 2, 29), 1, "Pacific/Honolulu") == datetime(
        2028, 2, 28, 19, tzinfo=UTC
    )


def test_validation_and_tool_credentials(context):
    _, client, households, body = setup_birthday(context)
    path = f"/api/birthdays/{uuid4()}"
    for invalid in (
        {"budget": "-1"},
        {"recipient": "  "},
        {"occasion_date": "2027-02-29"},
        {"lead_days": -1},
    ):
        assert client.put(path, json={**body, **invalid}).status_code == 422
    assert client.get("/api/tool/birthdays").status_code == 401
    client.headers["authorization"] = f"Bearer {households[0][1]}"
    assert client.put(path.replace("/api/", "/api/tool/"), json=body).status_code == 200
    assert len(client.get("/api/tool/birthdays").json()) == 1


def test_upcoming_complete_marks_birthday_ready(context):
    app, client, _, body = setup_birthday(context)
    birthday_id = str(uuid4())
    result = client.put(f"/api/birthdays/{birthday_id}", json=body).json()
    assert client.post(f"/api/tasks/{result['task_id']}/complete").status_code == 200
    assert client.get("/api/birthdays").json()[0]["gift_status"] == "ready"
    with app.state.sessions.begin() as db:
        tenant = db.get(Household, context[2][0][0])
        # A refreshed source event must never reopen preparation after completion.
        row = db.get(Birthday, birthday_id)
        row.calendar_id, row.event_id = "primary", "completed-event"
        import_calendar_event(
            db,
            tenant,
            body["conversation_id"],
            "primary",
            {
                "id": "completed-event",
                "start": {"date": "2027-02-20"},
            },
        )
        assert db.get(Task, result["task_id"]).status == "done"
