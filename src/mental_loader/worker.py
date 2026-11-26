"""Leased chat work and reminder delivery. Hermes cron continues to own reminder timing."""

import logging
import threading
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from mental_loader.accounts import subscription_authorized
from mental_loader.agents import HermesResponseError
from mental_loader.permissions import can_access
from mental_loader.push import send_push
from mental_loader.storage import Delivery, Message, PushSubscription, Run, Task, identifier, now

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120


@contextmanager
def heartbeat(sessions, model, row_id, token):
    stopped = threading.Event()

    def renew():
        while not stopped.wait(30):
            try:
                with sessions.begin() as db:
                    db.execute(
                        update(model)
                        .where(
                            model.id == row_id,
                            model.claim_token == token,
                            model.lease_until > now(),
                        )
                        .values(lease_until=now() + timedelta(seconds=LEASE_SECONDS))
                    )
            except Exception:
                logger.warning("Work lease renewal failed; stale completion will be fenced")

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=5)


def recover_runs(sessions, clock=None):
    # Only expired claims are interrupted. Never replay uncertain provider/tool work,
    # and never interrupt another live worker merely because this process starts.
    clock = clock or now()
    with sessions.begin() as db:
        candidates = db.scalars(
            select(Run.id)
            .where(
                Run.status == "running",
                or_(Run.lease_until.is_(None), Run.lease_until <= clock),
            )
            .with_for_update(skip_locked=True)
        )
        for run_id in list(candidates):
            result = "Work was interrupted. Check tasks before submitting again."
            claimed = db.execute(
                update(Run)
                .where(
                    Run.id == run_id,
                    Run.status == "running",
                    or_(Run.lease_until.is_(None), Run.lease_until <= clock),
                )
                .values(status="interrupted", result=result, claim_token=None, lease_until=None)
            )
            if claimed.rowcount:
                run = db.get(Run, run_id)
                db.add(
                    Message(
                        household_id=run.household_id,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        content=result,
                    )
                )


def process_run(sessions, agent):
    recover_runs(sessions)
    token = identifier()
    running = aliased(Run)
    try:
        with sessions.begin() as db:
            run = db.scalar(
                select(Run)
                .where(
                    Run.status == "pending",
                    ~exists(
                        select(running.id).where(
                            running.conversation_id == Run.conversation_id,
                            running.status == "running",
                        )
                    ),
                )
                .order_by(Run.created_at, Run.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return
            if not can_access(db, run.household_id, run.conversation_id, run.requested_by):
                run.status, run.result = (
                    "cancelled",
                    "Access was revoked before this request started.",
                )
                return
            claimed = db.execute(
                update(Run)
                .where(
                    Run.id == run.id,
                    Run.status == "pending",
                )
                .values(
                    status="running",
                    claim_token=token,
                    lease_until=now() + timedelta(seconds=LEASE_SECONDS),
                )
            )
            if not claimed.rowcount:
                return
            state = {
                "run_id": run.id,
                "household_id": run.household_id,
                "text": run.input,
                "claim_token": token,
            }
    except IntegrityError:
        # The unique running-conversation index fences simultaneous claims on
        # different pending messages of the same conversation, including on SQLite.
        return
    with heartbeat(sessions, Run, state["run_id"], token):
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
            completed = db.execute(
                update(Run)
                .where(
                    Run.id == state["run_id"],
                    Run.status == "running",
                    Run.claim_token == token,
                    Run.lease_until > now(),
                )
                .values(status=status, result=result, claim_token=None, lease_until=None)
            )
            if not completed.rowcount:
                return  # A stale worker must not overwrite interrupted/newer state.
            run = db.get(Run, state["run_id"])
            db.add(
                Message(
                    household_id=run.household_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content=result,
                )
            )


def enqueue_due(sessions, clock=None, household_id=None, allow_legacy=True):
    clock = clock or now()
    with sessions.begin() as db:
        query = select(Task).where(
            Task.status == "open",
            Task.notified_at.is_(None),
            Task.due_at <= clock,
        )
        if household_id is not None:
            query = query.where(Task.household_id == household_id)
        for task in db.scalars(query.with_for_update(skip_locked=True)):
            # CAS protects SQLite too; PostgreSQL locks also prevent concurrent completion.
            claimed = db.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.status == "open",
                    Task.notified_at.is_(None),
                )
                .values(notified_at=clock)
            )
            if not claimed.rowcount:
                continue
            db.add(
                Message(
                    household_id=task.household_id,
                    conversation_id=task.conversation_id,
                    role="assistant",
                    content=f"Reminder: {task.title}",
                )
            )
            for subscription in db.scalars(
                select(PushSubscription).where(
                    PushSubscription.household_id == task.household_id,
                )
            ):
                if not subscription_authorized(db, subscription, allow_legacy) or not can_access(
                    db,
                    task.household_id,
                    task.conversation_id,
                    subscription.user_id,
                ):
                    continue
                db.add(
                    Delivery(
                        household_id=task.household_id,
                        task_id=task.id,
                        subscription_id=subscription.id,
                        next_attempt_at=clock,
                    )
                )


def deliver_pending(sessions, settings, sender=send_push, clock=None, household_id=None):
    clock = clock or now()
    # Expired transport claims are at-least-once retries with stable notification tags.
    with sessions.begin() as db:
        query = update(Delivery).where(Delivery.status == "sending", Delivery.lease_until <= clock)
        if household_id is not None:
            query = query.where(Delivery.household_id == household_id)
        from sqlalchemy import case

        db.execute(
            query.values(
                status=case((Delivery.attempts >= 3, "failed"), else_="pending"),
                claim_token=None,
                lease_until=None,
            )
        )
        query = select(Delivery.id).where(
            Delivery.status == "pending",
            Delivery.next_attempt_at <= clock,
        )
        if household_id is not None:
            query = query.where(Delivery.household_id == household_id)
        delivery_ids = list(db.scalars(query.limit(20)))
    for delivery_id in delivery_ids:
        token = identifier()
        with sessions.begin() as db:
            claimed = db.execute(
                update(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.status == "pending",
                )
                .values(
                    status="sending",
                    claim_token=token,
                    attempts=Delivery.attempts + 1,
                    lease_until=clock + timedelta(seconds=LEASE_SECONDS),
                )
            )
            if not claimed.rowcount:
                continue
        with heartbeat(sessions, Delivery, delivery_id, token), sessions() as db:
            delivery = db.get(Delivery, delivery_id)
            task = db.get(Task, delivery.task_id)
            subscription = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.id == delivery.subscription_id,
                    PushSubscription.household_id == delivery.household_id,
                )
            )
            if (
                task is None
                or task.status != "open"
                or task.household_id != delivery.household_id
                or subscription is None
                or not subscription_authorized(db, subscription, not settings.hosted_mode)
                or not can_access(db, task.household_id, task.conversation_id, subscription.user_id)
            ):
                outcome = "cancelled"
            else:
                try:
                    outcome = sender(
                        subscription.subscription, task.id, task.conversation_id, settings
                    )
                except Exception as error:
                    logger.warning("Push transport failed: %s", type(error).__name__)
                    outcome = "pending" if delivery.attempts < 3 else "failed"
            db.execute(
                update(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.claim_token == token,
                    Delivery.status == "sending",
                )
                .values(
                    status=outcome,
                    claim_token=None,
                    lease_until=None,
                    next_attempt_at=clock + timedelta(seconds=60 * 2**delivery.attempts),
                )
            )
            if outcome == "expired" and subscription:
                db.delete(subscription)
            db.commit()


def complete_task(db, household_id, task_id):
    result = db.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.household_id == household_id,
        )
        .values(status="done")
    )
    return result.rowcount == 1
