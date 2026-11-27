"""Dated birthday preparation, independent of the agent runtime."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from mental_loader.storage import Birthday, Conversation, Delivery, Run, Task


class BirthdayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    recipient: str = Field(min_length=1, max_length=100)
    occasion_date: date
    preferences: str = Field(default="", max_length=1000)
    budget: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    lead_days: int = Field(default=14, ge=0, le=365)
    gift_status: str = Field(default="planning", pattern=r"^(planning|purchased|ready|cancelled)$")

    @field_validator("recipient")
    @classmethod
    def recipient_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Recipient must not be blank")
        return value.strip()


def preparation_deadline(day, lead_days, timezone):
    # Subtract calendar days before converting: preserve 09:00 across DST changes.
    local_day = day - timedelta(days=lead_days)
    return datetime.combine(local_day, time(9), ZoneInfo(timezone)).astimezone(UTC)


def birthday_dict(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "conversation_id",
            "recipient",
            "occasion_date",
            "preferences",
            "budget",
            "currency",
            "lead_days",
            "gift_status",
            "task_id",
            "calendar_id",
            "event_id",
        )
    }


def save_birthday(db, tenant, birthday_id, body):
    row = db.scalar(
        select(Birthday)
        .where(Birthday.id == birthday_id, Birthday.household_id == tenant.id)
        .with_for_update()
    )
    if row is None and db.get(Birthday, birthday_id):
        raise LookupError("Birthday not found")
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == body.conversation_id, Conversation.household_id == tenant.id
        )
    )
    from mental_loader.permissions import can_access

    if conversation is None or not can_access(
        db, tenant.id, body.conversation_id, getattr(tenant, "user_id", None)
    ):
        raise LookupError("Conversation not found")
    if row is not None and row.conversation_id != body.conversation_id:
        raise ValueError("A birthday cannot be moved to another conversation")
    if row is None:
        row = Birthday(id=birthday_id, household_id=tenant.id)
        db.add(row)
    elif row.calendar_id and row.occasion_date != body.occasion_date:
        raise ValueError("Update the source calendar date, then refresh the calendar import")
    for key, value in body.model_dump().items():
        setattr(row, key, str(value) if key == "budget" and value is not None else value)
    reconcile_task(db, tenant, row)
    db.flush()
    return row


def reconcile_task(db, tenant, row):
    task = db.get(Task, row.task_id) if row.task_id else None
    due = preparation_deadline(row.occasion_date, row.lead_days, tenant.timezone)
    title = f"Prepare birthday gift for {row.recipient} ({row.occasion_date.isoformat()})"
    if task is None:
        run = Run(
            household_id=tenant.id,
            conversation_id=row.conversation_id,
            request_id=str(uuid5(NAMESPACE_URL, f"mental-loader:birthday:{row.id}")),
            input="Birthday preparation",
            status="completed",
            requested_by=getattr(tenant, "user_id", None),
        )
        db.add(run)
        db.flush()
        task = Task(
            household_id=tenant.id,
            conversation_id=row.conversation_id,
            run_id=run.id,
            title=title,
            due_at=due,
        )
        db.add(task)
        db.flush()
        row.task_id = task.id
    previous_due = task.due_at
    if previous_due.tzinfo is None:
        previous_due = previous_due.replace(tzinfo=UTC)
    changed = previous_due != due or task.title != title
    terminal = row.gift_status in {"ready", "cancelled"}
    if changed or terminal:
        for delivery in db.scalars(
            select(Delivery).where(Delivery.task_id == task.id, Delivery.status == "pending")
        ):
            delivery.status = "cancelled"
    # Sent reminders remain receipts. A date correction does not resend automatically.
    task.title, task.due_at = title, due
    task.conversation_id = row.conversation_id
    task.status = "done" if terminal else "open"


def import_calendar_event(db, tenant, conversation_id, calendar_id, event):
    """Import only explicitly selected dated events; absence never implies cancellation."""
    from mental_loader.permissions import can_access

    if not can_access(db, tenant.id, conversation_id, getattr(tenant, "user_id", None)):
        raise LookupError("Conversation not found")
    row = db.scalar(
        select(Birthday)
        .where(
            Birthday.household_id == tenant.id,
            Birthday.calendar_id == calendar_id,
            Birthday.event_id == event["id"],
        )
        .with_for_update()
    )
    if row and (row.conversation_id != conversation_id or not can_access(
        db, tenant.id, row.conversation_id, getattr(tenant, "user_id", None)
    )):
        raise LookupError("Birthday not found in this conversation")
    if event.get("status") == "cancelled":
        if row:
            row.gift_status = "cancelled"
            reconcile_task(db, tenant, row)
        return row
    if event.get("recurrence"):
        raise ValueError("Select a dated recurring-event instance, not its series master")
    start = event.get("start", {})
    if start.get("date"):
        day = date.fromisoformat(start["date"])
    else:
        stamp = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Calendar event requires a timezone")
        day = stamp.astimezone(ZoneInfo(tenant.timezone)).date()
    if row is None:
        from mental_loader.storage import identifier

        body = BirthdayInput(
            conversation_id=conversation_id,
            recipient=event.get("summary", "Birthday"),
            occasion_date=day,
        )
        row = save_birthday(db, tenant, identifier(), body)
        row.calendar_id, row.event_id = calendar_id, event["id"]
    else:
        row.occasion_date = day
        # Preserve user preferences, budget and completed/cancelled preparation.
        reconcile_task(db, tenant, row)
    db.flush()
    return row
