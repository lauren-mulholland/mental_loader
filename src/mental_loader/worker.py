"""Chat request processing and portable reminder actions. Hermes cron owns reminder timing."""

import logging
from datetime import timedelta

from sqlalchemy import select, update

from mental_loader.agents import HermesResponseError
from mental_loader.push import send_push
from mental_loader.storage import Delivery, Message, PushSubscription, Run, Task, now

logger = logging.getLogger(__name__)


def recover_runs(sessions):
    # Don't replay possibly charged model calls after an interrupted process.
    with sessions.begin() as db:
        for run in db.scalars(select(Run).where(Run.status == "running")):
            run.status = "interrupted"
            run.result = "Work was interrupted by a restart. Check tasks before submitting again."
            db.add(
                Message(
                    household_id=run.household_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content=run.result,
                )
            )


def process_run(sessions, agent):
    with sessions.begin() as db:
        run = db.scalar(
            select(Run)
            .where(Run.status == "pending")
            .order_by(Run.created_at, Run.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            return
        run.status = "running"
        state = {"run_id": run.id, "household_id": run.household_id, "text": run.input}
    try:
        result = agent.invoke(state)["reply"]
        status = "completed"
    except Exception as error:
        code = error.code if isinstance(error, HermesResponseError) else type(error).__name__
        logger.warning("Agent run failed run_id=%s category=%s", state["run_id"], code)
        result = (
            "This request failed. Check Upcoming before retrying; a task may already exist. "
            f"Reference: {state['run_id']}."
        )
        status = "failed"
    with sessions.begin() as db:
        run = db.get(Run, state["run_id"])
        run.status, run.result = status, result
        db.add(
            Message(
                household_id=run.household_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                role="assistant",
                content=result,
            )
        )


def enqueue_due(sessions, clock=None, household_id=None):
    clock = clock or now()
    with sessions.begin() as db:
        query = select(Task).where(
            Task.status == "open", Task.notified_at.is_(None), Task.due_at <= clock
        )
        if household_id is not None:
            query = query.where(Task.household_id == household_id)
        tasks = db.scalars(query.with_for_update(skip_locked=True))
        for task in tasks:
            db.add(
                Message(
                    household_id=task.household_id,
                    conversation_id=task.conversation_id,
                    role="assistant",
                    content=f"Reminder: {task.title}",
                )
            )
            for subscription in db.scalars(
                select(PushSubscription).where(PushSubscription.household_id == task.household_id)
            ):
                db.add(
                    Delivery(
                        household_id=task.household_id,
                        task_id=task.id,
                        subscription_id=subscription.id,
                        next_attempt_at=clock,
                    )
                )
            task.notified_at = clock


def deliver_pending(sessions, settings, sender=send_push, clock=None, household_id=None):
    clock = clock or now()
    with sessions() as db:
        query = select(Delivery.id).where(
            Delivery.status == "pending", Delivery.next_attempt_at <= clock
        )
        if household_id is not None:
            query = query.where(Delivery.household_id == household_id)
        delivery_ids = list(db.scalars(query.limit(20)))
    for delivery_id in delivery_ids:
        with sessions() as db:
            delivery = db.get(Delivery, delivery_id)
            task = db.get(Task, delivery.task_id)
            subscription = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.id == delivery.subscription_id,
                    PushSubscription.household_id == delivery.household_id,
                )
            )
            if task.status != "open" or subscription is None:
                outcome = "cancelled"
            else:
                try:
                    outcome = sender(
                        subscription.subscription, task.id, task.conversation_id, settings
                    )
                except Exception as error:
                    logger.warning("Push transport failed: %s", type(error).__name__)
                    outcome = "pending" if delivery.attempts < 2 else "failed"
            delivery.attempts += 1
            delivery.status = outcome
            delivery.next_attempt_at = clock + timedelta(seconds=60 * 2**delivery.attempts)
            if outcome == "expired" and subscription:
                db.delete(subscription)
            db.commit()


def complete_task(db, household_id, task_id):
    result = db.execute(
        update(Task)
        .where(Task.id == task_id, Task.household_id == household_id)
        .values(status="done")
    )
    return result.rowcount == 1
