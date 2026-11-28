"""Explicitly opted-in Google birthday import, driven by the existing no-agent tick."""

import json
from datetime import date, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select, update

from mental_loader.accounts import Principal
from mental_loader.application import ApplicationError
from mental_loader.birthdays import import_calendar_event, reconcile_task
from mental_loader.integrations import calendar_events
from mental_loader.planning import Planning, Scope
from mental_loader.storage import (
    Birthday,
    Conversation,
    Household,
    IntegrationConnection,
    Membership,
    OccasionSettings,
    Run,
    digest,
    now,
)


class SyncInput(Scope):
    enabled: bool
    review_id: UUID | None = None
    selected: list[int] = Field(default_factory=list, max_length=1000)


def identity(event):
    return (
        event.get("birthdayProperties", {}).get("contact")
        or event.get("recurringEventId")
        or event["id"]
    )


def candidate(event, today):
    day = event.get("start", {}).get("date")
    if (
        not event.get("id")
        or event.get("status") == "cancelled"
        or event.get("eventType") != "birthday"
        or event.get("birthdayProperties", {}).get("type", "birthday") != "birthday"
        or not day
        or date.fromisoformat(day) < today
    ):
        return None
    return {
        "key": digest(identity(event)),
        "name": (event.get("summary") or "").strip()[:100] or "Birthday",
        "date": day,
    }


def candidates_for(events, tenant):
    today = now().astimezone(ZoneInfo(tenant.timezone)).date()
    result = {}
    for event in events:
        item = candidate(event, today)
        if item and (item["key"] not in result or item["date"] < result[item["key"]]["date"]):
            result[item["key"]] = item
    return sorted(result.values(), key=lambda item: (item["name"].casefold(), item["date"]))


class BirthdaySync(Planning):
    def __init__(self, sessions, settings):
        super().__init__(sessions)
        self.config = settings

    def require_owner(self, db, tenant, scope):
        self.require_conversation(db, tenant, scope)
        if tenant.role != "owner" or db.get(Conversation, scope).visibility != "household":
            raise ApplicationError(403, "An owner must choose a shared chat for birthday sync")

    def discover(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions() as db:
            self.require_owner(db, tenant, scope)
            _, doc = self.settings(db, tenant, scope)
            config = doc.get("sync", {})
            if config.get("user_id", tenant.user_id) != tenant.user_id:
                raise ApplicationError(403, "Only the calendar owner can manage this selection")
            if not db.get(IntegrationConnection, (tenant.id, tenant.user_id, "google-calendar")):
                raise ApplicationError(422, "Connect Google Calendar in Settings first")
        try:
            events = calendar_events(
                self.sessions, self.config, tenant.id, tenant.user_id, birthdays=True
            )
        except LookupError as exc:
            raise ApplicationError(422, "Connect Google Calendar in Settings first") from exc
        except Exception as exc:
            raise ApplicationError(
                502, "Could not read Google Calendar. Check the connection."
            ) from exc
        entries = candidates_for(events, tenant)
        with self.sessions.begin() as db:
            self.require_owner(db, tenant, scope)
            row, doc = self.settings(db, tenant, scope, write=True)
            config = doc.get("sync", {})
            if config.get("user_id", tenant.user_id) != tenant.user_id:
                raise ApplicationError(403, "Only the calendar owner can manage this selection")
            selected = set(config.get("selected", []))
            missing = selected - {entry["key"] for entry in entries}
            # Preserve a previously selected person if the provider omits them temporarily.
            entries.extend(
                {"key": key, "name": "Previously selected · not found in Calendar", "date": None}
                for key in sorted(missing)
            )
            if len(entries) > 1000:
                raise ApplicationError(422, "There are too many birthdays to review at once")
            review_id = str(uuid4())
            doc["calendar_review"] = {
                "id": review_id,
                "user_id": tenant.user_id,
                "keys": [item["key"] for item in entries],
                "expires": (now() + timedelta(minutes=30)).isoformat(),
            }
            self.save_settings(row, doc)
            return {
                "review_id": review_id,
                "people": [
                    {
                        "name": item["name"],
                        "date": item["date"],
                        "selected": item["key"] in selected,
                        "new": item["key"] in config.get("pending", []),
                    }
                    for item in entries
                ],
            }

    def configure(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions.begin() as db:
            db.execute(
                update(Household).where(Household.id == tenant.id).values(name=Household.name)
            )
            self.require_owner(db, tenant, scope)
            row, doc = self.settings(db, tenant, scope, write=True)
            config = doc.get("sync", {})
            if config.get("user_id", tenant.user_id) != tenant.user_id:
                raise ApplicationError(403, "Only the calendar owner can manage this selection")
            if not body.enabled:
                doc["sync"] = {
                    **config,
                    "user_id": tenant.user_id,
                    "paused": True,
                    "lease": None,
                    "lease_until": "",
                }
                self.save_settings(row, doc)
                return {"enabled": False}
            if not db.get(IntegrationConnection, (tenant.id, tenant.user_id, "google-calendar")):
                raise ApplicationError(422, "Connect Google Calendar in Settings first")
            for other in self.sources(db, tenant.id):
                other_sync = json.loads(other.document).get("sync", {})
                if (
                    other.conversation_id != scope
                    and other_sync.get("selected") is not None
                    and not other_sync.get("paused")
                ):
                    raise ApplicationError(409, "Turn off sync in the other shared chat first")
            review = doc.get("calendar_review", {})
            if (
                not body.review_id
                or review.get("id") != str(body.review_id)
                or review.get("user_id") != tenant.user_id
                or review["expires"] <= now().isoformat()
            ):
                raise ApplicationError(409, "Review calendar birthdays before saving the selection")
            keys = review["keys"]
            if any(index < 0 or index >= len(keys) for index in body.selected):
                raise ApplicationError(422, "Invalid birthday selection")
            selected = {keys[index] for index in body.selected}
            doc["sync"] = {
                "user_id": tenant.user_id,
                "state": "waiting",
                "selected": sorted(selected),
                "known": sorted(set(config.get("known", [])) | set(keys)),
                "pending": sorted(set(config.get("pending", [])) - set(keys)),
            }
            doc.pop("calendar_review", None)
            self.reconcile_selection(db, tenant, scope, doc, selected)
            self.save_settings(row, doc)
            return {"enabled": True, "selected_count": len(selected)}

    def reconcile_selection(self, db, tenant, scope, doc, selected):
        source = f"google:{tenant.user_id}:primary"
        prefix = source + ":"
        # Older rows stored the series-to-person link but had no per-instance key.
        legacy_keys = {
            person: digest(key.removeprefix(prefix))
            for key, person in doc.get("calendar_people", {}).items()
            if key.startswith(prefix)
        }
        birthdays = db.scalars(
            select(Birthday)
            .where(
                Birthday.conversation_id == scope,
                Birthday.calendar_id == source,
            )
            .with_for_update()
        )
        for birthday in birthdays:
            key = (
                birthday.calendar_key
                or legacy_keys.get(birthday.person_id)
                or digest(birthday.event_id)
            )
            birthday.calendar_key = key
            excluded = key not in selected
            if birthday.sync_excluded == excluded:
                continue
            birthday.sync_excluded = excluded
            reconcile_task(db, tenant, birthday)
            if excluded:
                request_id = str(
                    uuid5(NAMESPACE_URL, f"mental-loader:birthday-suggestions:{birthday.id}")
                )
                db.execute(
                    update(Run)
                    .where(
                        Run.household_id == tenant.id,
                        Run.request_id == request_id,
                        Run.status == "pending",
                    )
                    .values(status="cancelled")
                )

    def status(self, tenant, body):
        with self.sessions() as db:
            _, doc = self.settings(db, tenant, str(body.conversation_id))
            sync = doc.get("sync", {})
            enabled = "selected" in sync and not sync.get("paused")
            managing = (
                tenant.role == "owner" and sync.get("user_id", tenant.user_id) == tenant.user_id
            )
            return {
                "enabled": enabled,
                "state": sync.get("state")
                if enabled
                else "review"
                if sync and not sync.get("paused")
                else "off",
                "last_success": sync.get("last_success") if enabled else None,
                "selected_count": len(sync.get("selected", [])),
                "new_count": len(sync.get("pending", [])) if enabled and managing else 0,
                "can_manage": managing,
            }

    def sources(self, db, household_id):
        return db.scalars(
            select(OccasionSettings)
            .join(Conversation)
            .where(
                Conversation.household_id == household_id, Conversation.visibility == "household"
            )
            .order_by(OccasionSettings.conversation_id)
        )

    def due(self, household_id):
        with self.sessions() as db:
            stamp = now().isoformat()
            for row in self.sources(db, household_id):
                sync = json.loads(row.document).get("sync")
                if (
                    sync
                    and "selected" in sync
                    and not sync.get("paused")
                    and sync.get("next_attempt", "") <= stamp
                ):
                    return row.conversation_id
        return None

    def run(self, tenant, body, *, force=False):
        scope = str(body.conversation_id)
        token = str(uuid4())
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, scope, write=True)
            sync = doc.get("sync")
            if sync is None or "selected" not in sync or sync.get("paused"):
                raise ApplicationError(422, "Review calendar birthdays and turn on sync first")
            if force and (tenant.role != "owner" or tenant.user_id != sync["user_id"]):
                raise ApplicationError(403, "Only the calendar owner can refresh this connection")
            stamp = now()
            deadline = (stamp + timedelta(minutes=5)).isoformat()
            if sync.get("lease_until", "") > stamp.isoformat():
                return {"state": "syncing"}
            if not force and sync.get("next_attempt", "") > stamp.isoformat():
                return {"state": sync["state"]}
            sync.update(lease=token, lease_until=deadline, next_attempt=deadline, state="syncing")
            self.save_settings(row, doc)
            user_id = sync["user_id"]

        try:
            events = calendar_events(self.sessions, self.config, tenant.id, user_id, birthdays=True)
            with self.sessions.begin() as db:
                row, doc = self.settings(db, tenant, scope, write=True)
                sync = doc.get("sync", {})
                if sync.get("lease") != token:
                    return {"state": "superseded"}
                member = db.get(Membership, (tenant.id, user_id))
                if (
                    not member
                    or member.role != "owner"
                    or not db.get(IntegrationConnection, (tenant.id, user_id, "google-calendar"))
                ):
                    raise LookupError("Birthday calendar is disconnected")
                actor = Principal(tenant.household, user_id, member.role)
                count = self.import_events(db, actor, scope, doc, user_id, events)
                found = {entry["key"] for entry in candidates_for(events, actor)}
                sync["pending"] = sorted(found - set(sync["known"]))
                sync.update(
                    state="synced",
                    last_success=now().isoformat(),
                    next_attempt=(now() + timedelta(days=7)).isoformat(),
                )
                sync.pop("lease_until", None)
                self.save_settings(row, doc)
                return {"state": "synced", "count": count}
        except Exception:
            with self.sessions.begin() as db:
                row, doc = self.settings(db, tenant, scope, write=True)
                sync = doc.get("sync", {})
                if sync.get("lease") == token:
                    sync.update(
                        state="unavailable", next_attempt=(now() + timedelta(hours=1)).isoformat()
                    )
                    sync.pop("lease_until", None)
                    self.save_settings(row, doc)
            return {"state": "unavailable"}

    def import_events(self, db, actor, scope, doc, user_id, events):
        source = f"google:{user_id}:primary"
        series = doc.setdefault("calendar_people", {})
        count = 0
        selected = set(doc["sync"]["selected"])
        today = now().astimezone(ZoneInfo(actor.timezone)).date()
        for event in events:
            event_id = event.get("id")
            if not event_id or len(event_id) > 1024:
                continue
            existing = db.scalar(
                select(Birthday).where(
                    Birthday.household_id == actor.id,
                    Birthday.calendar_id == source,
                    Birthday.event_id == event_id,
                )
            )
            if existing and existing.conversation_id != scope:
                continue  # Never move records between conversations.
            if event.get("status") == "cancelled":
                if existing:
                    import_calendar_event(db, actor, scope, source, event)
                continue
            item = candidate(event, today)
            if not item or item["key"] not in selected:
                continue
            if existing:
                existing.sync_excluded = False
            birthday = import_calendar_event(
                db, actor, scope, source, {**event, "summary": item["name"]}
            )
            birthday.calendar_key = item["key"]
            if not birthday.actions:
                series_key = f"{source}:{identity(event)}"
                birthday.person_id = series.get(series_key)
                person = self.associate(doc, birthday)
                series[series_key] = birthday.person_id
                self.apply_plan(db, actor, birthday, self.effective(doc, person))
            count += 1
        return count
