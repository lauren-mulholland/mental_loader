"""Application operations shared by browser APIs and runtime tools.

Credentials are resolved by transport adapters. Operations accept a server-created
Principal, enforce its scope, and own transactions. No Hermes or HTTP dependencies.
"""

from datetime import UTC
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mental_loader.accounts import Principal
from mental_loader.birthdays import BirthdayInput, birthday_dict, save_birthday
from mental_loader.domain import complete_task
from mental_loader.permissions import can_access, visible_conversations
from mental_loader.storage import (
    Birthday,
    Conversation,
    Delivery,
    Membership,
    Message,
    Run,
    Task,
    now,
)


class ReminderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    due_at: str = Field(min_length=10, max_length=50)
    request_id: UUID
    conversation_id: UUID | None = None


class ApplicationError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class HouseholdOperations:
    def __init__(self, sessions):
        self.sessions = sessions

    def require_actor(self, db, tenant: Principal):
        if tenant.user_id and db.get(Membership, (tenant.id, tenant.user_id)) is None:
            raise ApplicationError(401, "Household membership is no longer active")
        if tenant.conversation_id:
            self.require_conversation(db, tenant, tenant.conversation_id)

    def require_conversation(self, db, tenant: Principal, conversation_id):
        if tenant.conversation_id and tenant.conversation_id != conversation_id:
            raise ApplicationError(404, "Conversation not found")
        if not can_access(db, tenant.id, conversation_id, tenant.user_id):
            raise ApplicationError(404, "Conversation not found")

    def birthdays(self, tenant: Principal):
        with self.sessions() as db:
            self.require_actor(db, tenant)
            visible = select(Conversation.id).where(
                visible_conversations(tenant.id, tenant.user_id)
            )
            query = select(Birthday).where(
                Birthday.household_id == tenant.id,
                Birthday.conversation_id.in_(visible),
                Birthday.sync_excluded.is_(False),
            )
            if tenant.conversation_id:
                query = query.where(Birthday.conversation_id == tenant.conversation_id)
            return [
                birthday_dict(row)
                for row in db.scalars(query.order_by(Birthday.occasion_date, Birthday.id))
            ]

    def save_birthday(self, tenant: Principal, birthday_id: str, body: BirthdayInput):
        try:
            with self.sessions.begin() as db:
                self.require_actor(db, tenant)
                self.require_conversation(db, tenant, body.conversation_id)
                previous = db.get(Birthday, birthday_id)
                if previous:
                    self.require_conversation(db, tenant, previous.conversation_id)
                return birthday_dict(save_birthday(db, tenant, birthday_id, body))
        except LookupError as error:
            raise ApplicationError(404, str(error)) from error
        except ValueError as error:
            raise ApplicationError(422, str(error)) from error
        except IntegrityError as error:
            raise ApplicationError(409, "Concurrent update; refresh before retrying") from error

    def complete_task(self, tenant: Principal, task_id: str):
        with self.sessions.begin() as db:
            self.require_actor(db, tenant)
            task = db.scalar(select(Task).where(Task.id == task_id, Task.household_id == tenant.id))
            if task is None:
                raise ApplicationError(404, "Task not found")
            self.require_conversation(db, tenant, task.conversation_id)
            if not complete_task(db, tenant.id, task_id):
                raise ApplicationError(404, "Task not found")
        return {"ok": True}

    def tasks(self, tenant: Principal):
        with self.sessions() as db:
            self.require_actor(db, tenant)
            query = (
                select(Task)
                .join(Conversation, Task.conversation_id == Conversation.id)
                .where(
                    Task.household_id == tenant.id,
                    visible_conversations(tenant.id, getattr(tenant, "user_id", None)),
                )
                .order_by(Task.due_at)
            )
            if tenant.conversation_id:
                query = query.where(Task.conversation_id == tenant.conversation_id)
            rows = db.scalars(query)
            deliveries = db.scalars(select(Delivery).where(Delivery.household_id == tenant.id))
            statuses = {}
            for delivery in deliveries:
                statuses.setdefault(delivery.task_id, []).append(delivery.status)
            return [
                {
                    "id": r.id,
                    "conversation_id": r.conversation_id,
                    "title": r.title,
                    "due_at": as_utc(r.due_at),
                    "status": r.status,
                    "push_status": statuses.get(r.id, []),
                    "notified_at": r.notified_at,
                }
                for r in rows
            ]

    def remind(self, tenant: Principal, body: ReminderInput):
        from mental_loader.domain import create_reminder

        command = f"/remind {body.due_at} {body.title}"
        try:
            with self.sessions.begin() as db:
                self.require_actor(db, tenant)
                existing = db.scalar(
                    select(Run).where(
                        Run.household_id == tenant.id, Run.request_id == str(body.request_id)
                    )
                )
                if existing:
                    self.require_conversation(db, tenant, existing.conversation_id)
                    if (
                        body.conversation_id
                        and str(body.conversation_id) != existing.conversation_id
                    ):
                        raise ApplicationError(
                            409, "Request ID already used in another conversation"
                        )
                    if existing.input != command:
                        raise ApplicationError(409, "Request ID already used with different input")
                    task = db.scalar(select(Task).where(Task.run_id == existing.id))
                    if task is None:
                        raise ApplicationError(
                            409, "Request already exists without a completed task"
                        )
                    return {
                        "task_id": task.id,
                        "conversation_id": task.conversation_id,
                        "title": task.title,
                        "due_at": as_utc(task.due_at).isoformat(),
                    }
                selected = (
                    str(body.conversation_id) if body.conversation_id else tenant.conversation_id
                )
                if selected:
                    conversation_id = selected
                    self.require_conversation(db, tenant, conversation_id)
                else:
                    row = Conversation(household_id=tenant.id)
                    db.add(row)
                    db.flush()
                    conversation_id = row.id
                run = Run(
                    household_id=tenant.id,
                    conversation_id=conversation_id,
                    request_id=str(body.request_id),
                    input=command,
                    status="completed",
                    requested_by=tenant.user_id,
                )
                db.add(run)
                db.flush()
                try:
                    task = create_reminder(db, run, tenant.timezone, now())
                except ValueError as error:
                    raise ApplicationError(422, str(error)) from error
                run.result = f"Saved a reminder: {task.title}."
                db.add(
                    Message(
                        household_id=tenant.id,
                        conversation_id=conversation_id,
                        run_id=run.id,
                        role="assistant",
                        content=run.result,
                    )
                )
                return {
                    "task_id": task.id,
                    "conversation_id": conversation_id,
                    "title": task.title,
                    "due_at": task.due_at.isoformat(),
                }
        except IntegrityError as error:
            raise ApplicationError(409, "Concurrent request; retry the same request ID") from error
