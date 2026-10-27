"""Deterministic household rules. No agent-framework imports."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from mental_loader.storage import Run, Task


def parse_reminder(text: str, timezone: str, clock: datetime) -> tuple[str, datetime]:
    """Explicit commands avoid pretending the no-model spike understands free text."""
    match = re.fullmatch(r"/remind\s+(\d{1,5})([mh])\s+(.+)", text.strip(), re.DOTALL)
    if match:
        amount, unit, title = match.groups()
        minutes = int(amount) * (60 if unit == "h" else 1)
        if not 1 <= minutes <= 525600:
            raise ValueError("Choose a delay between one minute and one year.")
        due = clock + timedelta(minutes=minutes)
    else:
        match = re.fullmatch(r"/remind\s+(\S+)\s+(.+)", text.strip(), re.DOTALL)
        if not match:
            raise ValueError("Use /remind 1m Buy a birthday card")
        raw, title = match.groups()
        try:
            due = datetime.fromisoformat(raw)
        except ValueError as error:
            raise ValueError("Use an ISO date/time, including its UTC offset.") from error
        if due.tzinfo is None:
            raise ValueError("Include a UTC offset, e.g. 2026-11-01T09:00:00+11:00.")
    title = title.strip()
    if not title or len(title) > 240:
        raise ValueError("Reminder titles must contain 1–240 characters.")
    if due <= clock:
        raise ValueError("The reminder must be in the future.")
    # Validate household timezone even for offset-aware input.
    due.astimezone(ZoneInfo(timezone))
    return title, due.astimezone(UTC)


def create_reminder(db: Session, run: Run, timezone: str, clock: datetime) -> Task:
    existing = db.scalar(select(Task).where(Task.run_id == run.id))
    if existing:
        return existing
    title, due = parse_reminder(run.input, timezone, clock)
    task = Task(
        household_id=run.household_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        title=title,
        due_at=due,
    )
    db.add(task)
    db.flush()
    return task
