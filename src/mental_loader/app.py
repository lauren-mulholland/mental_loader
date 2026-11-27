import asyncio
import base64
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mental_loader.agents import build_agent
from mental_loader.config import Settings
from mental_loader.push import validate_endpoint
from mental_loader.storage import (
    Conversation,
    Delivery,
    Household,
    LoginSession,
    Message,
    PushSubscription,
    Run,
    Task,
    database,
    digest,
    now,
)
from mental_loader.worker import (
    complete_task,
    deliver_pending,
    enqueue_due,
    process_run,
    recover_runs,
)

STATIC = Path(__file__).parent / "static"


class LoginInput(BaseModel):
    access_code: str = Field(min_length=32, max_length=128)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2000)
    request_id: UUID


class ToolReminderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    due_at: str = Field(min_length=10, max_length=50)
    request_id: UUID
    conversation_id: UUID | None = None


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(max_length=2048)
    keys: dict[str, str]

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        return validate_endpoint(value)

    @field_validator("keys")
    @classmethod
    def valid_keys(cls, value):
        for name, size in (("p256dh", 65), ("auth", 16)):
            raw = value.get(name, "")
            if len(raw) > 200:
                raise ValueError("Invalid subscription key")
            try:
                decoded = base64.b64decode(
                    raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True
                )
            except ValueError as error:
                raise ValueError("Invalid subscription key") from error
            if len(decoded) != size or (name == "p256dh" and decoded[0] != 4):
                raise ValueError("Invalid subscription key")
        return {key: value[key] for key in ("p256dh", "auth")}


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def create_app(settings=None, start_worker=True):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)
    agent = build_agent(sessions, settings)

    async def worker():
        while True:
            try:
                await asyncio.to_thread(process_run, sessions, agent)
            except Exception:
                # No request text, subscriptions, credentials or model errors in logs.
                import logging

                logging.getLogger(__name__).warning("Worker tick failed; retrying in 2 seconds")
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if start_worker:
            recover_runs(sessions)
            task = asyncio.create_task(worker())
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        engine.dispose()

    app = FastAPI(title="Mental Loader feasibility", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.agent = agent

    @app.get("/healthz")
    def readiness():
        from sqlalchemy import text

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True}

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Required on every browser mutation, including login/logout.
            tool_request = request.url.path.startswith("/api/tool/")
            if not tool_request and request.headers.get("origin") != settings.public_origin.rstrip(
                "/"
            ):
                return Response(status_code=403, content="Origin not allowed")
            if int(request.headers.get("content-length", "0")) > 8192:
                return Response(status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def household(request: Request):
        token = request.cookies.get("ml_session", "")
        with sessions() as db:
            login = db.get(LoginSession, digest(token)) if token else None
            if not login or as_utc(login.expires_at) <= now():
                raise HTTPException(401, "Sign in to your household")
            return db.get(Household, login.household_id)

    def conversation_for(db, household_id, conversation_id):
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.household_id == household_id
            )
        )
        if not conversation:
            raise HTTPException(404, "Conversation not found")
        return conversation

    def tool_household(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Household tool credential required")
        with sessions() as db:
            tenant = db.scalar(
                select(Household).where(
                    Household.access_hash == digest(authorization.removeprefix("Bearer "))
                )
            )
            if tenant is None:
                raise HTTPException(401, "Invalid household tool credential")
            return tenant

    @app.get("/api/tool/clock")
    def tool_clock(tenant=Depends(tool_household)):
        return {"utc": now().isoformat(), "timezone": tenant.timezone}

    @app.post("/api/tool/dispatch-reminders")
    def dispatch_reminders(tenant=Depends(tool_household)):
        # Hermes cron owns timing. This endpoint performs one deterministic action;
        # it is not a second scheduler and cannot dispatch another household's records.
        enqueue_due(sessions, household_id=tenant.id)
        if settings.vapid_public_key:
            deliver_pending(sessions, settings, household_id=tenant.id)
        return {"ok": True}

    @app.get("/api/tool/tasks")
    def tool_tasks(tenant=Depends(tool_household)):
        return tasks(tenant)

    @app.post("/api/tool/reminders")
    def tool_remind(body: ToolReminderInput, tenant=Depends(tool_household)):
        from mental_loader.domain import create_reminder

        command = f"/remind {body.due_at} {body.title}"
        try:
            with sessions.begin() as db:
                existing = db.scalar(
                    select(Run).where(
                        Run.household_id == tenant.id, Run.request_id == str(body.request_id)
                    )
                )
                if existing:
                    if existing.input != command:
                        raise HTTPException(409, "Request ID already used with different input")
                    task = db.scalar(select(Task).where(Task.run_id == existing.id))
                    if task is None:
                        raise HTTPException(409, "Request already exists without a completed task")
                    return {
                        "task_id": task.id,
                        "conversation_id": task.conversation_id,
                        "title": task.title,
                        "due_at": as_utc(task.due_at).isoformat(),
                    }
                if body.conversation_id:
                    conversation_id = str(body.conversation_id)
                    conversation_for(db, tenant.id, conversation_id)
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
                )
                db.add(run)
                db.flush()
                try:
                    task = create_reminder(db, run, tenant.timezone, now())
                except ValueError as error:
                    raise HTTPException(422, str(error)) from error
                run.result = f"Hermes saved a reminder: {task.title}."
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
            raise HTTPException(409, "Concurrent request; retry the same request ID") from error

    @app.get("/health")
    def health():
        return {"status": "ok", "phase": "technical-feasibility"}

    @app.post("/api/login")
    def login(body: LoginInput, response: Response):
        with sessions.begin() as db:
            tenant = db.scalar(
                select(Household).where(Household.access_hash == digest(body.access_code))
            )
            if tenant is None:
                raise HTTPException(401, "Invalid access code")
            token = secrets.token_urlsafe(32)
            db.add(
                LoginSession(
                    token_hash=digest(token),
                    household_id=tenant.id,
                    expires_at=now() + timedelta(days=7),
                )
            )
            response.set_cookie(
                "ml_session",
                token,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="strict",
                max_age=604800,
            )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        with sessions.begin() as db:
            login = db.get(LoginSession, digest(request.cookies.get("ml_session", "")))
            if login:
                db.delete(login)
        response.delete_cookie("ml_session")
        return {"ok": True}

    @app.get("/api/me")
    def me(tenant=Depends(household)):
        return {
            "household": tenant.name,
            "timezone": tenant.timezone,
            "ai_enabled": settings.ai_enabled,
            "push_configured": bool(settings.vapid_public_key),
            "vapid_public_key": settings.vapid_public_key,
            "calendar": "Google Calendar — OAuth probe via CLI; sync not enabled",
        }

    @app.get("/api/conversations")
    def conversations(tenant=Depends(household)):
        with sessions() as db:
            rows = db.scalars(
                select(Conversation)
                .where(Conversation.household_id == tenant.id)
                .order_by(Conversation.created_at.desc())
            )
            return [{"id": row.id, "created_at": row.created_at} for row in rows]

    @app.post("/api/conversations")
    def new_conversation(tenant=Depends(household)):
        with sessions.begin() as db:
            row = Conversation(household_id=tenant.id)
            db.add(row)
            db.flush()
            return {"id": row.id}

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str, tenant=Depends(household)):
        with sessions() as db:
            conversation_for(db, tenant.id, conversation_id)
            rows = db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id, Message.household_id == tenant.id
                )
                .order_by(Message.created_at, Message.id)
            )
            runs = db.scalars(
                select(Run).where(
                    Run.conversation_id == conversation_id,
                    Run.household_id == tenant.id,
                    Run.status.in_(["pending", "running"]),
                )
            )
            return {
                "messages": [
                    {"id": r.id, "role": r.role, "content": r.content, "created_at": r.created_at}
                    for r in rows
                ],
                "active_runs": [{"id": r.id, "status": r.status} for r in runs],
            }

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    def send_message(conversation_id: str, body: MessageInput, tenant=Depends(household)):
        if not body.content.strip():
            raise HTTPException(422, "A message cannot be blank")
        try:
            with sessions.begin() as db:
                conversation_for(db, tenant.id, conversation_id)
                existing = db.scalar(
                    select(Run).where(
                        Run.household_id == tenant.id, Run.request_id == str(body.request_id)
                    )
                )
                if existing:
                    if (
                        existing.conversation_id != conversation_id
                        or existing.input != body.content
                    ):
                        raise HTTPException(409, "Request ID was already used for another message")
                    return {"run_id": existing.id, "status": existing.status}
                count = len(
                    list(
                        db.scalars(
                            select(Run.id)
                            .where(
                                Run.household_id == tenant.id,
                                Run.status.in_(["pending", "running"]),
                            )
                            .limit(5)
                        )
                    )
                )
                if count >= 5:
                    raise HTTPException(429, "Please wait for your pending requests")
                row = Run(
                    household_id=tenant.id,
                    conversation_id=conversation_id,
                    request_id=str(body.request_id),
                    input=body.content,
                )
                db.add(row)
                db.flush()
                db.add(
                    Message(
                        household_id=tenant.id,
                        conversation_id=conversation_id,
                        run_id=row.id,
                        role="user",
                        content=body.content,
                    )
                )
                return {"run_id": row.id, "status": row.status}
        except IntegrityError as error:
            raise HTTPException(
                409, "Request is already being submitted; retry the same ID"
            ) from error

    @app.get("/api/tasks")
    def tasks(tenant=Depends(household)):
        with sessions() as db:
            rows = db.scalars(
                select(Task).where(Task.household_id == tenant.id).order_by(Task.due_at)
            )
            deliveries = db.scalars(select(Delivery).where(Delivery.household_id == tenant.id))
            statuses = {}
            for delivery in deliveries:
                statuses.setdefault(delivery.task_id, []).append(delivery.status)
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "due_at": as_utc(r.due_at),
                    "status": r.status,
                    "push_status": statuses.get(r.id, []),
                    "notified_at": r.notified_at,
                }
                for r in rows
            ]

    @app.post("/api/tasks/{task_id}/complete")
    def complete(task_id: str, tenant=Depends(household)):
        with sessions.begin() as db:
            if not complete_task(db, tenant.id, task_id):
                raise HTTPException(404, "Task not found")
        return {"ok": True}

    @app.post("/api/push-subscriptions")
    def subscribe(body: SubscriptionInput, tenant=Depends(household)):
        if not settings.vapid_public_key:
            raise HTTPException(503, "Generate and configure VAPID keys first")
        with sessions.begin() as db:
            row = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.endpoint_hash == digest(body.endpoint)
                )
            )
            if row and row.household_id != tenant.id:
                raise HTTPException(409, "Unsubscribe this browser before switching households")
            if row is None:
                row = PushSubscription(household_id=tenant.id, endpoint_hash=digest(body.endpoint))
                db.add(row)
            row.subscription = body.model_dump()
        return {"ok": True}

    @app.delete("/api/push-subscriptions")
    def unsubscribe(body: SubscriptionInput, tenant=Depends(household)):
        with sessions.begin() as db:
            row = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.endpoint_hash == digest(body.endpoint),
                    PushSubscription.household_id == tenant.id,
                )
            )
            if row:
                db.delete(row)
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(
            STATIC / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
