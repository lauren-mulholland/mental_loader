from datetime import timedelta
from uuid import uuid4

from conftest import login
from test_accounts import account

from mental_loader.storage import IntegrationConnection, now


def rpc(client, headers, method, params=None):
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
    )
    return response.json()["result"]


def call(client, headers, name, **arguments):
    return rpc(client, headers, "tools/call", {"name": name, "arguments": arguments})


def seed(client, households):
    login(client, households[0][1])
    chat = client.post("/api/conversations").json()["id"]
    body = {"conversation_id": chat}
    birthday = client.put(
        f"/api/events/birthdays/{uuid4()}",
        json={
            **body,
            "recipient": "Alex",
            "occasion_date": (now() + timedelta(days=35)).date().isoformat(),
        },
    )
    assert birthday.status_code == 200
    return body, birthday.json()


def test_reviewed_mcp_workflow(context):
    _, client, households, _ = context
    body, birthday = seed(client, households)
    headers = {"Authorization": "Bearer " + households[0][1]}
    assert client.post("/mcp").status_code == 401
    assert (
        rpc(client, headers, "initialize", {"protocolVersion": "2025-11-25"})["protocolVersion"]
        == "2025-11-25"
    )
    assert {item["name"] for item in rpc(client, headers, "tools/list")["tools"]} >= {
        "list_people",
        "save_gift_idea",
        "preview_bulk_gift_settings",
    }
    person = birthday["person_id"]
    assert set(birthday["plan"]["actions"]) == {"gift", "call"}
    idea = {**body, "person_id": person, "idea": "Pottery class", "request_id": str(uuid4())}
    assert not call(client, headers, "save_gift_idea", **idea)["isError"]
    assert not call(client, headers, "save_gift_idea", **idea)["isError"]
    board = client.get("/api/events/" + body["conversation_id"]).json()
    assert len(board["people"][0]["ideas"]) == 1
    assert call(client, {"Authorization": "Bearer " + households[1][1]}, "list_people", **body)[
        "isError"
    ]

    proposal = call(
        client, headers, "preview_bulk_gift_settings", **body, budget=40, cards=True, scope="both"
    )["structuredContent"]
    assert len(proposal["changes"]) == 2
    assert (
        client.post(
            "/api/events/apply",
            headers={"Origin": "https://invalid.test"},
            json={
                **body,
                "preview_id": proposal["preview_id"],
                "request_id": str(uuid4()),
            },
        ).status_code
        == 403
    )
    # An intervening direct edit invalidates the exact reviewed snapshot.
    payload = {
        **body,
        "defaults": {**board["defaults"], "budget": 70},
        "overrides": {},
        "revision": board["revision"],
    }
    assert client.put("/api/events/people", json=payload).status_code == 200
    apply = {**body, "preview_id": proposal["preview_id"], "request_id": str(uuid4())}
    assert client.post("/api/events/apply", json=apply).status_code == 409

    proposal = client.post(
        "/api/events/preview",
        json={
            **body,
            "budget": 45,
            "cards": True,
            "scope": "both",
        },
    ).json()
    apply["preview_id"] = proposal["preview_id"]
    assert (
        call(client, headers, "get_gift_edit_receipt", **body, preview_id=apply["preview_id"])[
            "structuredContent"
        ]["state"]
        == "awaiting_review"
    )
    result = client.post("/api/events/apply", json=apply)
    assert result.status_code == 200 and result.json()["updated"] == 2
    assert client.post("/api/events/apply", json=apply).json() == result.json()
    assert (
        call(client, headers, "get_gift_edit_receipt", **body, preview_id=apply["preview_id"])[
            "structuredContent"
        ]
        == result.json()
    )
    assert any("birthday card" in task["title"] for task in client.get("/api/tasks").json())

    undo = client.post("/api/events/undo", json={**body, "preview_id": apply["preview_id"]})
    assert undo.status_code == 200 and undo.json()["state"] == "undone"
    restored = client.get("/api/events/" + body["conversation_id"]).json()["people"][0]
    assert restored["override"] is None and restored["effective"]["budget"] == 70
    assert all(
        "birthday card" not in task["title"] or task["status"] != "open"
        for task in client.get("/api/tasks").json()
    )


def test_calendar_sync_is_opt_in_scoped_and_idempotent(context, monkeypatch):
    app, client, households, _ = context
    owner_id, code = account(app, households[0][0])
    login(client, code)
    chat = client.post("/api/conversations").json()["id"]
    scope = {"conversation_id": chat}
    with app.state.sessions.begin() as db:
        db.add(
            IntegrationConnection(
                household_id=households[0][0],
                user_id=owner_id,
                provider="google-calendar",
                encrypted_credentials="fixture",
            )
        )
    date = (now() + timedelta(days=20)).date().isoformat()
    events = [
        {
            "id": "instance-1",
            "recurringEventId": "stable",
            "eventType": "birthday",
            "summary": "Sam",
            "start": {"date": date},
        },
        {
            "id": "meeting",
            "eventType": "default",
            "summary": "Private meeting",
            "start": {"date": date},
        },
    ]
    seen = []

    def fetch(sessions, settings, household_id, user_id, *, birthdays):
        seen.append((household_id, user_id, birthdays))
        return events

    monkeypatch.setattr("mental_loader.birthday_sync.calendar_events", fetch)
    assert client.post("/api/events/sync", json=scope).status_code == 422
    assert client.put("/api/events/sync", json={**scope, "enabled": True}).status_code == 409
    review = client.post("/api/events/sync/people", json=scope).json()
    assert review["people"] == [{"name": "Sam", "date": date, "selected": False, "new": False}]
    assert client.get("/api/birthdays").json() == []
    assert (
        client.put(
            "/api/events/sync",
            json={
                **scope,
                "enabled": True,
                "review_id": review["review_id"],
                "selected": [0],
            },
        ).status_code
        == 200
    )
    assert client.post("/api/events/sync", json=scope).json()["count"] == 1
    assert client.post("/api/events/sync", json=scope).json()["count"] == 1
    assert seen == [(households[0][0], owner_id, True)] * 3
    board = client.get("/api/events/" + chat).json()
    assert len(board["people"]) == len(board["birthdays"]) == 1
    assert board["birthdays"][0]["recipient"] == "Sam"
    assert len(client.get("/api/tasks").json()) == 3
    events[:] = [{"id": "instance-1", "status": "cancelled"}]
    assert client.post("/api/events/sync", json=scope).status_code == 200
    assert client.get("/api/birthdays").json()[0]["gift_status"] == "cancelled"
    assert all(t["status"] != "open" for t in client.get("/api/tasks").json())


def test_birthday_nudge_queues_at_most_one_model_request(context):
    from sqlalchemy import select

    from mental_loader.storage import Run
    from mental_loader.worker import enqueue_due

    app, client, households, _ = context
    body, birthday = seed(client, households)
    clock = now() + timedelta(days=24)
    for _ in range(2):
        enqueue_due(
            app.state.sessions, clock=clock, household_id=households[0][0], suggest_gifts=True
        )
    with app.state.sessions() as db:
        pending = list(db.scalars(select(Run).where(Run.status == "pending")))
        assert len(pending) == 1 and birthday["id"] in pending[0].input
        assert pending[0].conversation_id == body["conversation_id"]


def test_shared_mcp_cannot_use_private_people(context):
    app, client, households, _ = context
    _, code = account(app, households[0][0])
    login(client, code)
    private = client.post("/api/conversations", json={"visibility": "private"}).json()["id"]
    result = client.post(
        "/api/events/people",
        json={
            "conversation_id": private,
            "name": "Private person",
            "person_id": str(uuid4()),
        },
    )
    assert result.status_code == 200
    headers = {"Authorization": "Bearer " + households[0][1]}
    denied = call(client, headers, "list_people", conversation_id=private)
    assert denied["isError"] and denied["structuredContent"]["status"] == 404


def test_weekly_sync_surfaces_new_birthdays_without_importing_until_selected(context, monkeypatch):
    from mental_loader import birthday_sync

    app, client, households, _ = context
    household_id, tool_code = households[0]
    owner_id, code = account(app, household_id)
    _, member_code = account(app, household_id, "Another member", "member")
    login(client, code)
    chat = client.post("/api/conversations").json()["id"]
    scope = {"conversation_id": chat}
    with app.state.sessions.begin() as db:
        db.add(
            IntegrationConnection(
                household_id=household_id,
                user_id=owner_id,
                provider="google-calendar",
                encrypted_credentials="fixture",
            )
        )
    start = now()
    stamp = [start]
    monkeypatch.setattr(birthday_sync, "now", lambda: stamp[0])
    date = (start + timedelta(days=30)).date().isoformat()

    def item(id_, name):
        return {
            "id": id_,
            "recurringEventId": id_ + "-annual",
            "eventType": "birthday",
            "summary": name,
            "start": {"date": date},
        }

    events = [item("alex", "Alex"), item("sam", "Sam")]
    monkeypatch.setattr(birthday_sync, "calendar_events", lambda *args, **kwargs: list(events))
    status_url = "/api/events/" + chat + "/sync"

    def discover():
        reply = client.post("/api/events/sync/people", json=scope)
        assert reply.status_code == 200
        return reply.json()

    def save(review, names):
        positions = [i for i, person in enumerate(review["people"]) if person["name"] in names]
        return client.put(
            "/api/events/sync",
            json={
                **scope,
                "enabled": True,
                "review_id": review["review_id"],
                "selected": positions,
            },
        )

    assert save(discover(), {"Alex"}).status_code == 200
    assert client.post("/api/events/sync", json=scope).json()["count"] == 1
    original = client.get("/api/birthdays").json()[0]
    assert original["recipient"] == "Alex"

    events.extend([item("jo", "Jo"), item("kim", "Kim")])
    entry = "/api/tool/dispatch-reminders"
    headers = {"Authorization": "Bearer " + tool_code}
    stamp[0] = start + timedelta(days=6)
    assert client.post(entry, headers=headers).status_code == 200
    assert client.get(status_url).json()["new_count"] == 0

    stamp[0] = start + timedelta(days=7, seconds=1)
    assert client.post(entry, headers=headers).status_code == 200
    assert client.get(status_url).json()["new_count"] == 2
    assert [b["recipient"] for b in client.get("/api/birthdays").json()] == ["Alex"]
    login(client, member_code)
    assert client.get(status_url).json()["new_count"] == 0
    assert client.post("/api/events/sync/people", json=scope).status_code == 403

    login(client, code)
    review = discover()
    assert {p["name"] for p in review["people"] if p["new"]} == {"Jo", "Kim"}
    assert {p["name"] for p in review["people"] if p["selected"]} == {"Alex"}
    assert save(review, set()).status_code == 200
    assert client.get("/api/birthdays").json() == []
    assert all(task["status"] != "open" for task in client.get("/api/tasks").json())
    assert client.get(status_url).json()["new_count"] == 0
    assert save(review, {"Sam"}).status_code == 409

    assert save(discover(), {"Alex", "Jo"}).status_code == 200
    assert client.post("/api/events/sync", json=scope).json()["count"] == 2
    imported = {b["recipient"]: b for b in client.get("/api/birthdays").json()}
    assert set(imported) == {"Alex", "Jo"}
    assert imported["Alex"]["id"] == original["id"]
    assert imported["Alex"]["task_id"] == original["task_id"]
