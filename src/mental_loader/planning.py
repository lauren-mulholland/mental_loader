"""Application operations for people, birthday plans and reviewed edits."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, update

from mental_loader.application import ApplicationError, HouseholdOperations
from mental_loader.birthdays import BirthdayInput, birthday_dict, reconcile_task, save_birthday
from mental_loader.storage import Birthday, Conversation, GiftEditPreview, OccasionSettings, now

Action = Literal["gift", "call", "card", "meal", "flowers", "experience", "delivery", "cake"]
DEFAULT = {"actions": ["gift", "call"], "budget": 50, "lead_days": 7, "nudge_days": 14}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Input):
    conversation_id: UUID


class Defaults(Input):
    actions: set[Action] = Field(max_length=8)
    budget: int = Field(ge=0, le=100_000)
    lead_days: int = Field(ge=0, le=90)
    nudge_days: int = Field(ge=0, le=90)


class PeopleInput(Scope):
    defaults: Defaults
    overrides: dict[UUID, Defaults | None] = Field(max_length=100)
    revision: int = Field(ge=0)
    update_upcoming: bool = False


class PersonInput(Scope):
    name: str = Field(min_length=1, max_length=100)
    person_id: UUID


class ManualBirthdayInput(BirthdayInput):
    person_id: UUID | None = None


class IdeaInput(Scope):
    person_id: UUID
    idea: str = Field(min_length=1, max_length=500)
    request_id: UUID


class RemoveIdeaInput(Scope):
    person_id: UUID
    idea_id: UUID


class BulkInput(Scope):
    person_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    budget: int | None = Field(default=None, ge=0, le=100_000)
    cards: bool | None = None
    scope: Literal["defaults", "occasions", "both"]

    @model_validator(mode="after")
    def changes_present(self):
        if self.budget is None and self.cards is None:
            raise ValueError("Choose a budget or card change")
        return self


class PreviewInput(Scope):
    preview_id: UUID


class ApplyInput(PreviewInput):
    request_id: UUID


class BirthdayPlan(Scope, Defaults):
    birthday_id: UUID


def pack(model):
    result = model.model_dump(mode="json")
    result["actions"] = sorted(result["actions"])
    return result


class Planning(HouseholdOperations):
    def settings(self, db, tenant, conversation_id, *, write=False):
        self.require_conversation(db, tenant, conversation_id)
        if write:
            # Serialize planning writes and preview approval on both supported databases.
            db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(visibility=Conversation.visibility)
            )
        row = db.get(OccasionSettings, conversation_id)
        if row is None and write:
            row = OccasionSettings(conversation_id=conversation_id, revision=0, document="{}")
            db.add(row)
        doc = json.loads(row.document) if row else {}
        doc.setdefault("defaults", DEFAULT.copy())
        doc.setdefault("people", {})
        return row, doc

    def save_settings(self, row, doc):
        row.document = json.dumps(doc)
        row.revision += 1

    def birthday_rows(self, db, scope):
        return list(
            db.scalars(
                select(Birthday)
                .where(Birthday.conversation_id == scope, Birthday.sync_excluded.is_(False))
                .order_by(Birthday.occasion_date, Birthday.id)
            )
        )

    def effective(self, doc, person):
        return person["override"] or doc["defaults"]

    def people_data(self, row, doc):
        return {
            "revision": row.revision if row else 0,
            "defaults": doc["defaults"],
            "people": [
                {
                    "id": key,
                    "name": p["name"],
                    "override": p["override"],
                    "effective": self.effective(doc, p),
                    "ideas": [{"id": id_, "text": text} for id_, text in p["ideas"].items()],
                }
                for key, p in doc["people"].items()
            ],
        }

    def read(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions() as db:
            row, doc = self.settings(db, tenant, scope)
            return {
                **self.people_data(row, doc),
                "birthdays": [birthday_dict(b) for b in self.birthday_rows(db, scope)],
            }

    def associate(self, doc, birthday):
        from mental_loader.storage import identifier

        roster = doc["people"]
        if birthday.person_id in roster:
            return roster[birthday.person_id]
        # Do not merge different people solely on name.
        key = identifier()
        birthday.person_id = key
        roster[key] = {"name": birthday.recipient, "override": None, "ideas": {}}
        return roster[key]

    def add_birthday(self, tenant, birthday_id, body: BirthdayInput):
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, body.conversation_id, write=True)
            birthday = db.get(Birthday, birthday_id)
            if birthday is not None:
                if birthday.conversation_id != body.conversation_id:
                    raise ApplicationError(404, "Birthday not found")
                return birthday_dict(birthday)
            data = BirthdayInput.model_validate(body.model_dump(exclude={"person_id"}))
            if body.person_id and str(body.person_id) not in doc["people"]:
                raise ApplicationError(404, "Person not found")
            if body.person_id:
                data.recipient = doc["people"][str(body.person_id)]["name"]
            birthday = save_birthday(db, tenant, birthday_id, data)
            birthday.person_id = str(body.person_id) if body.person_id else None
            person = self.associate(doc, birthday)
            self.apply_plan(db, tenant, birthday, self.effective(doc, person))
            self.save_settings(row, doc)
            return birthday_dict(birthday)

    def add_person(self, tenant, body):
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, str(body.conversation_id), write=True)
            key, name = str(body.person_id), body.name.strip()
            if not name:
                raise ApplicationError(422, "Enter a name")
            person = doc["people"].get(key)
            if person and name != person["name"]:
                raise ApplicationError(409, "Person ID already used")
            if not person:
                if len(doc["people"]) >= 100:
                    raise ApplicationError(422, "This plan already has 100 people")
                doc["people"][key] = {"name": name, "override": None, "ideas": {}}
                self.save_settings(row, doc)
            return {"person_id": key, "name": name}

    def update_people(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, scope, write=True)
            if row.revision != body.revision:
                raise ApplicationError(409, "Settings changed. Reload and review your choices.")
            if any(str(key) not in doc["people"] for key in body.overrides):
                raise ApplicationError(404, "Person not found")
            doc["defaults"] = pack(body.defaults)
            for key, value in body.overrides.items():
                doc["people"][str(key)]["override"] = pack(value) if value else None
            if body.update_upcoming:
                for birthday in self.birthday_rows(db, scope):
                    if not self.upcoming(tenant, birthday):
                        continue
                    old = json.loads(birthday.actions or "{}")
                    if old.get("custom"):
                        continue
                    person = self.associate(doc, birthday)
                    self.apply_plan(db, tenant, birthday, self.effective(doc, person))
            self.save_settings(row, doc)
            return self.people_data(row, doc)

    def upcoming(self, tenant, birthday):
        from zoneinfo import ZoneInfo

        return birthday.occasion_date >= now().astimezone(ZoneInfo(tenant.timezone)).date()

    def apply_plan(self, db, tenant, birthday, plan):
        birthday.actions = json.dumps(plan)
        birthday.budget = str(Decimal(plan["budget"]))
        birthday.lead_days = plan["lead_days"]
        reconcile_task(db, tenant, birthday)

    def edit_plan(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, scope, write=True)
            birthday = db.get(Birthday, str(body.birthday_id))
            if birthday is None or birthday.conversation_id != scope or birthday.sync_excluded:
                raise ApplicationError(404, "Birthday not found")
            self.associate(doc, birthday)
            plan = pack(body)
            for key in ("conversation_id", "birthday_id"):
                plan.pop(key)
            self.apply_plan(db, tenant, birthday, {**plan, "custom": True})
            self.save_settings(row, doc)
            return birthday_dict(birthday)

    def save_idea(self, tenant, body):
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, str(body.conversation_id), write=True)
            person = doc["people"].get(str(body.person_id))
            if person is None:
                raise ApplicationError(404, "Person not found")
            idea = body.idea.strip()
            if not idea:
                raise ApplicationError(422, "Enter a gift idea")
            key = str(body.request_id)
            if key in person["ideas"] and person["ideas"][key] != idea:
                raise ApplicationError(409, "Request ID already used")
            if key not in person["ideas"]:
                if len(person["ideas"]) >= 100:
                    raise ApplicationError(422, "This person already has 100 saved ideas")
                person["ideas"][key] = idea
                self.save_settings(row, doc)
            return {"idea_id": key, "person_id": str(body.person_id), "idea": idea}

    def remove_idea(self, tenant, body):
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, str(body.conversation_id), write=True)
            person = doc["people"].get(str(body.person_id))
            if person is None:
                raise ApplicationError(404, "Person not found")
            if person["ideas"].pop(str(body.idea_id), None) is not None:
                self.save_settings(row, doc)
            return {"ok": True}

    def change_set(self, db, tenant, row, doc, body):
        people = doc["people"]
        ids = list(dict.fromkeys(map(str, body.person_ids))) if body.person_ids else list(people)
        if not ids or len(ids) > 100 or any(key not in people for key in ids):
            raise ApplicationError(422, "Choose up to 100 accessible people")
        changes = []
        if body.scope in {"defaults", "both"}:
            for key in ids:
                before = self.effective(doc, people[key])
                changes.append(
                    {
                        "kind": "default",
                        "id": key,
                        "name": people[key]["name"],
                        "before": before,
                        "before_override": people[key]["override"],
                        "after": self.changed(before, body),
                    }
                )
        if body.scope in {"occasions", "both"}:
            for birthday in self.birthday_rows(db, str(body.conversation_id)):
                if birthday.person_id not in ids or not self.upcoming(tenant, birthday):
                    continue
                before = json.loads(birthday.actions or "{}")
                if not before or birthday.gift_status == "cancelled" or birthday.sync_excluded:
                    continue
                changes.append(
                    {
                        "kind": "birthday",
                        "id": birthday.id,
                        "name": birthday.recipient,
                        "date": birthday.occasion_date.isoformat(),
                        "before": before,
                        "after": self.changed(before, body) | {"custom": True},
                    }
                )
        if not changes or len(changes) > 100:
            raise ApplicationError(422, "Choose between 1 and 100 affected records")
        return {"revision": row.revision, "changes": changes, "scope": body.scope}

    @staticmethod
    def changed(before, body):
        patch = dict(before)
        if body.budget is not None:
            patch["budget"] = body.budget
        if body.cards is not None:
            actions = set(patch["actions"])
            actions.add("card") if body.cards else actions.discard("card")
            patch["actions"] = sorted(actions)
        return patch

    def preview(self, tenant, body):
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, str(body.conversation_id), write=True)
            stamp = now()
            db.execute(
                delete(GiftEditPreview).where(
                    GiftEditPreview.conversation_id == row.conversation_id,
                    GiftEditPreview.created < (stamp - timedelta(days=1)).isoformat(),
                )
            )
            proposal = self.change_set(db, tenant, row, doc, body)
            preview = GiftEditPreview(
                conversation_id=row.conversation_id,
                created=stamp.isoformat(),
                document=json.dumps(proposal),
            )
            db.add(preview)
            db.flush()
            return {"preview_id": preview.id, **proposal}

    def previews(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions() as db:
            self.require_conversation(db, tenant, scope)
            return [
                {"preview_id": p.id, **json.loads(p.document)}
                for p in db.scalars(
                    select(GiftEditPreview)
                    .where(
                        GiftEditPreview.conversation_id == scope,
                        GiftEditPreview.request_id.is_(None),
                        GiftEditPreview.created >= (now() - timedelta(minutes=30)).isoformat(),
                    )
                    .order_by(GiftEditPreview.created.desc())
                    .limit(5)
                )
            ]

    def apply(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, scope, write=True)
            preview = db.get(GiftEditPreview, str(body.preview_id))
            if preview is None or preview.conversation_id != scope:
                raise ApplicationError(404, "Preview not found")
            proposal = json.loads(preview.document)
            if preview.request_id == str(body.request_id):
                return proposal["receipt"]
            if preview.request_id:
                raise ApplicationError(409, "Preview already applied")
            if tenant.role in {"runtime", "legacy-tool"}:
                raise ApplicationError(403, "Use Apply changes in the signed-in app")
            if (
                row.revision != proposal["revision"]
                or preview.created < (now() - timedelta(minutes=30)).isoformat()
            ):
                raise ApplicationError(409, "This preview is stale. Request a new preview.")
            for change in proposal["changes"]:
                if change["kind"] == "default":
                    person = doc["people"][change["id"]]
                    person["override"] = change["after"]
                else:
                    birthday = db.get(Birthday, change["id"], with_for_update=True)
                    if (
                        not birthday
                        or birthday.conversation_id != scope
                        or birthday.occasion_date.isoformat() != change["date"]
                        or birthday.recipient != change["name"]
                        or json.loads(birthday.actions or "{}") != change["before"]
                        or not self.upcoming(tenant, birthday)
                        or birthday.gift_status == "cancelled"
                        or birthday.sync_excluded
                    ):
                        raise ApplicationError(409, "A birthday changed. Request a new preview.")
                    self.apply_plan(db, tenant, birthday, change["after"])
            self.save_settings(row, doc)
            proposal["receipt"] = {
                "preview_id": preview.id,
                "updated": len(proposal["changes"]),
                "records": [c["id"] for c in proposal["changes"]],
            }
            proposal["applied_revision"] = row.revision
            preview.request_id = str(body.request_id)
            preview.document = json.dumps(proposal)
            return proposal["receipt"]

    def undo(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions.begin() as db:
            row, doc = self.settings(db, tenant, scope, write=True)
            preview = db.get(GiftEditPreview, str(body.preview_id))
            if preview is None or preview.conversation_id != scope or not preview.request_id:
                raise ApplicationError(404, "Applied edit not found")
            proposal = json.loads(preview.document)
            if proposal.get("undone"):
                return proposal["receipt"]
            if row.revision != proposal["applied_revision"]:
                raise ApplicationError(409, "Planning settings changed after this edit")
            for change in proposal["changes"]:
                if change["kind"] == "default":
                    doc["people"][change["id"]]["override"] = change["before_override"]
                else:
                    birthday = db.get(Birthday, change["id"], with_for_update=True)
                    if (
                        not birthday
                        or birthday.conversation_id != scope
                        or birthday.occasion_date.isoformat() != change["date"]
                        or birthday.recipient != change["name"]
                        or json.loads(birthday.actions or "{}") != change["after"]
                        or not self.upcoming(tenant, birthday)
                        or birthday.gift_status == "cancelled"
                        or birthday.sync_excluded
                    ):
                        raise ApplicationError(409, "A birthday changed after this edit")
                    self.apply_plan(db, tenant, birthday, change["before"])
            self.save_settings(row, doc)
            proposal["undone"] = True
            proposal["receipt"] = {**proposal["receipt"], "state": "undone"}
            preview.document = json.dumps(proposal)
            return proposal["receipt"]

    def receipt(self, tenant, body):
        scope = str(body.conversation_id)
        with self.sessions() as db:
            self.require_conversation(db, tenant, scope)
            preview = db.get(GiftEditPreview, str(body.preview_id))
            if preview is None or preview.conversation_id != scope:
                raise ApplicationError(404, "Preview not found")
            return json.loads(preview.document).get("receipt", {"state": "awaiting_review"})
