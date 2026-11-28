import asyncio
import base64
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mental_loader.accounts import Principal, enrolled, remove_member, rotate_credentials
from mental_loader.agents import build_agent
from mental_loader.application import ApplicationError, HouseholdOperations, ReminderInput
from mental_loader.birthday_sync import BirthdaySync, SyncInput
from mental_loader.birthdays import BirthdayInput
from mental_loader.config import Settings
from mental_loader.mcp_interface import dispatch as dispatch_mcp
from mental_loader.permissions import visible_conversations
from mental_loader.planning import (
    ApplyInput,
    BirthdayPlan,
    BulkInput,
    IdeaInput,
    ManualBirthdayInput,
    PeopleInput,
    PersonInput,
    PreviewInput,
    RemoveIdeaInput,
    Scope,
)
from mental_loader.push import validate_endpoint
from mental_loader.security import BodyLimit, rate_limit, validate_hosted
from mental_loader.storage import (
    AIUsage,
    Conversation,
    ConversationParticipant,
    Household,
    IntegrationConnection,
    LoginSession,
    Membership,
    Message,
    PushSubscription,
    Run,
    RuntimeBinding,
    User,
    database,
    digest,
    now,
)
from mental_loader.worker import (
    deliver_pending,
    enqueue_due,
    process_run,
    recover_runs,
)

STATIC = Path(__file__).parent / "static"


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_code: str = Field(min_length=32, max_length=128)
    household_id: UUID | None = None


class RecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recovery_code: str = Field(min_length=32, max_length=128)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2000)
    request_id: UUID


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["household", "private"] = "household"


class ParticipantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


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
    validate_hosted(settings)
    engine, sessions = database(settings.database_url)
    agent = build_agent(sessions, settings)
    operations = HouseholdOperations(sessions)
    planning = BirthdaySync(sessions, settings)

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
    app.add_middleware(BodyLimit)
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
            tool_request = request.url.path.startswith("/api/tool/") or request.url.path == "/mcp"
            if not tool_request and request.headers.get("origin") != settings.public_origin.rstrip(
                "/"
            ):
                return Response(status_code=403, content="Origin not allowed")
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return Response(status_code=400)
            if length < 0:
                return Response(status_code=400)
            if length > 8192:
                return Response(status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.path.startswith("/api/") or request.url.path == "/mcp":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.exception_handler(IntegrityError)
    async def integrity_conflict(request, error):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Data changed while saving. Refresh and try again.",
            },
        )

    def household(request: Request):
        token = request.cookies.get("ml_session", "")
        with sessions() as db:
            login = db.get(LoginSession, digest(token)) if token else None
            if not login or as_utc(login.expires_at) <= now():
                raise HTTPException(401, "Sign in to your household")
            tenant = db.get(Household, login.household_id)
            if login.user_id:
                membership = db.get(Membership, (login.household_id, login.user_id))
                if membership is None:
                    raise HTTPException(401, "Household membership is no longer active")
                return Principal(tenant, login.user_id, membership.role)
            if not settings.allow_legacy_login or enrolled(db, tenant.id):
                raise HTTPException(401, "Sign in with your personal access code")
            return Principal(tenant, None, "legacy")

    def personal(tenant=Depends(household)):
        if tenant.user_id is None:
            raise HTTPException(403, "Personal sign-in required")
        return tenant

    def quota(key, capacity, seconds=60):
        if not rate_limit(sessions, key, capacity, seconds):
            raise HTTPException(
                429,
                "Too many requests. Please try again later.",
                headers={"Retry-After": str(seconds)},
            )

    def conversation_for(db, household_id, conversation_id, user_id=None):
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, visible_conversations(household_id, user_id)
            )
        )
        if not conversation:
            raise HTTPException(404, "Conversation not found")
        return conversation

    def tool_household(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Household tool credential required")
        quota(f"tool-auth:{request.client.host}", 240)
        with sessions() as db:
            binding = db.scalar(
                select(RuntimeBinding).where(
                    RuntimeBinding.tool_hash == digest(authorization.removeprefix("Bearer "))
                )
            )
            if binding:
                from mental_loader.permissions import can_access

                tenant = db.get(Household, binding.household_id)
                user_id = None
                if binding.conversation_id:
                    conversation = db.get(Conversation, binding.conversation_id)
                    user_id = conversation.owner_id
                    if not can_access(db, tenant.id, conversation.id, user_id):
                        raise HTTPException(401, "Runtime creator no longer has access")
                return Principal(tenant, user_id, "runtime", binding.conversation_id, True)
            if settings.hosted_mode:
                if settings.paas_personal_runtime and secrets.compare_digest(
                    authorization.removeprefix("Bearer "), settings.hermes_tool_token
                ):
                    tenant = db.get(Household, settings.hermes_household_id)
                    if tenant and enrolled(db, tenant.id):
                        return Principal(tenant, None, "runtime", None, True)
                raise HTTPException(401, "Scoped runtime credential required")
            tenant = db.scalar(
                select(Household).where(
                    Household.access_hash == digest(authorization.removeprefix("Bearer "))
                )
            )
            if tenant is None:
                raise HTTPException(401, "Invalid household tool credential")
            return Principal(tenant, None, "legacy-tool")

    @app.get("/api/tool/clock")
    def tool_clock(tenant=Depends(tool_household)):
        return {"utc": now().isoformat(), "timezone": tenant.timezone}

    @app.post("/api/tool/dispatch-reminders")
    def dispatch_reminders(tenant=Depends(tool_household)):
        # Hermes cron owns timing. This endpoint performs one deterministic action;
        # it is not a second scheduler and cannot dispatch another household's records.
        if tenant.conversation_id:
            raise HTTPException(403, "Private runtimes cannot dispatch household jobs")
        enqueue_due(
            sessions,
            household_id=tenant.id,
            allow_legacy=not settings.hosted_mode,
            suggest_gifts=settings.ai_enabled,
        )
        if settings.vapid_public_key:
            deliver_pending(sessions, settings, household_id=tenant.id)
        due_scope = planning.due(tenant.id)
        if due_scope:
            planning.run(tenant, Scope(conversation_id=due_scope))
        return {"ok": True}

    @app.get("/api/tool/tasks")
    def tool_tasks(tenant=Depends(tool_household)):
        return operations.tasks(tenant)

    @app.post("/api/tool/reminders")
    def tool_remind(body: ReminderInput, tenant=Depends(tool_household)):
        return operations.remind(tenant, body)

    def action_principal(request):
        if request.url.path.startswith("/api/tool/"):
            return tool_household(request)
        return household(request)

    @app.get("/api/birthdays")
    @app.get("/api/tool/birthdays")
    def birthday_list(request: Request):
        tenant = action_principal(request)
        return operations.birthdays(tenant)

    @app.put("/api/birthdays/{birthday_id}")
    @app.put("/api/tool/birthdays/{birthday_id}")
    def birthday_save(birthday_id: UUID, body: BirthdayInput, request: Request):
        return operations.save_birthday(action_principal(request), str(birthday_id), body)

    @app.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    def application_mcp(
        request: Request, payload: object = Body(default=None), tenant=Depends(tool_household)
    ):
        origin = request.headers.get("origin")
        if origin and origin != settings.public_origin.rstrip("/"):
            raise HTTPException(403, "Origin not allowed")
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        return dispatch_mcp(payload, tenant, planning)

    @app.get("/api/events/{conversation_id}")
    def event_board(conversation_id: UUID, tenant=Depends(household)):
        return planning.read(tenant, Scope(conversation_id=conversation_id))

    @app.put("/api/events/birthdays/{birthday_id}")
    def add_event_birthday(birthday_id: UUID, body: ManualBirthdayInput, tenant=Depends(household)):
        return planning.add_birthday(tenant, str(birthday_id), body)

    @app.put("/api/events/birthday-plan")
    def edit_birthday_plan(body: BirthdayPlan, tenant=Depends(household)):
        return planning.edit_plan(tenant, body)

    @app.post("/api/events/people")
    def add_event_person(body: PersonInput, tenant=Depends(household)):
        return planning.add_person(tenant, body)

    @app.delete("/api/events/gift-ideas")
    def remove_gift_idea(body: RemoveIdeaInput, tenant=Depends(household)):
        return planning.remove_idea(tenant, body)

    @app.put("/api/events/people")
    def change_people(body: PeopleInput, tenant=Depends(household)):
        return planning.update_people(tenant, body)

    @app.post("/api/events/gift-ideas")
    def add_gift_idea(body: IdeaInput, tenant=Depends(household)):
        return planning.save_idea(tenant, body)

    @app.get("/api/events/{conversation_id}/previews")
    def list_gift_previews(conversation_id: UUID, tenant=Depends(household)):
        return planning.previews(tenant, Scope(conversation_id=conversation_id))

    @app.post("/api/events/preview")
    def preview_gift_edit(body: BulkInput, tenant=Depends(household)):
        return planning.preview(tenant, body)

    @app.post("/api/events/apply")
    def apply_gift_edit(body: ApplyInput, tenant=Depends(household)):
        return planning.apply(tenant, body)

    @app.post("/api/events/undo")
    def undo_gift_edit(body: PreviewInput, tenant=Depends(household)):
        return planning.undo(tenant, body)

    @app.get("/api/events/{conversation_id}/sync")
    def calendar_sync_status(conversation_id: UUID, tenant=Depends(household)):
        return planning.status(tenant, Scope(conversation_id=conversation_id))

    @app.post("/api/events/sync/people")
    def discover_calendar_birthdays(body: Scope, tenant=Depends(personal)):
        quota(f"birthday-review:{tenant.user_id}", 6, 300)
        return planning.discover(tenant, body)

    @app.put("/api/events/sync")
    def configure_calendar_sync(body: SyncInput, tenant=Depends(personal)):
        return planning.configure(tenant, body)

    @app.post("/api/events/sync")
    def refresh_birthdays(body: Scope, tenant=Depends(personal)):
        quota(f"birthday-sync:{tenant.user_id}", 4, 300)
        return planning.run(tenant, body, force=True)

    @app.get("/health")
    def health():
        return {"status": "ok", "phase": "technical-feasibility"}

    @app.post("/api/login")
    def login(body: LoginInput, request: Request, response: Response):
        quota(f"login-ip:{request.client.host}", 60, 600)
        quota(f"login-code:{digest(body.access_code)}", 20, 600)
        with sessions.begin() as db:
            user = db.scalar(select(User).where(User.access_hash == digest(body.access_code)))
            user_id = None
            tenant = None
            if user:
                query = select(Membership).where(Membership.user_id == user.id)
                if body.household_id:
                    query = query.where(Membership.household_id == str(body.household_id))
                memberships = list(db.scalars(query))
                if len(memberships) > 1:
                    raise HTTPException(409, "Choose a household ID to sign in")
                if memberships:
                    tenant = db.get(Household, memberships[0].household_id)
                    user_id = user.id
            elif settings.allow_legacy_login:
                tenant = db.scalar(
                    select(Household).where(Household.access_hash == digest(body.access_code))
                )
                if tenant and (
                    enrolled(db, tenant.id)
                    or (body.household_id and str(body.household_id) != tenant.id)
                ):
                    tenant = None
            if tenant is None:
                raise HTTPException(401, "Invalid access code")
            from sqlalchemy import delete

            device = request.cookies.get("ml_device", "")
            if not (32 <= len(device) <= 128):
                device = secrets.token_urlsafe(32)
            db.execute(
                delete(PushSubscription).where(PushSubscription.device_hash == digest(device))
            )
            previous = db.get(LoginSession, digest(request.cookies.get("ml_session", "")))
            if previous:
                db.delete(previous)
            token = secrets.token_urlsafe(32)
            db.add(
                LoginSession(
                    token_hash=digest(token),
                    household_id=tenant.id,
                    user_id=user_id,
                    expires_at=now() + timedelta(days=7),
                )
            )
            response.set_cookie(
                "ml_session",
                token,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                max_age=604800,
            )
            response.set_cookie(
                "ml_device",
                device,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                max_age=31536000,
            )
        return {"ok": True}

    @app.post("/api/login/options")
    def login_options(body: LoginInput, request: Request):
        quota(f"login-ip:{request.client.host}", 60, 600)
        with sessions() as db:
            user = db.scalar(select(User).where(User.access_hash == digest(body.access_code)))
            if user:
                rows = db.scalars(
                    select(Household)
                    .join(Membership, Membership.household_id == Household.id)
                    .where(Membership.user_id == user.id)
                )
                choices = [{"id": row.id, "name": row.name} for row in rows]
                if choices:
                    return choices
            elif settings.allow_legacy_login:
                row = db.scalar(
                    select(Household).where(Household.access_hash == digest(body.access_code))
                )
                if row and not enrolled(db, row.id):
                    return [{"id": row.id, "name": row.name}]
            raise HTTPException(401, "Invalid access code")

    @app.post("/api/account/recover")
    def recover_account(body: RecoveryInput, request: Request, response: Response):
        quota(f"recovery-ip:{request.client.host}", 20, 3600)
        quota(f"recovery-code:{digest(body.recovery_code)}", 5, 3600)
        with sessions.begin() as db:
            user = db.scalar(
                select(User)
                .where(User.recovery_hash == digest(body.recovery_code))
                .with_for_update()
            )
            if user is None:
                raise HTTPException(401, "Invalid or already-used recovery code")
            from sqlalchemy import update

            claimed = db.execute(
                update(User)
                .where(User.id == user.id, User.recovery_hash == digest(body.recovery_code))
                .values(recovery_hash=None)
            )
            if not claimed.rowcount:
                raise HTTPException(401, "Invalid or already-used recovery code")
            result = rotate_credentials(db, user)
        response.delete_cookie("ml_session")
        return result

    @app.post("/api/account/rotate")
    def rotate_account(body: LoginInput, response: Response, tenant=Depends(personal)):
        quota(f"rotate:{tenant.user_id}", 5, 3600)
        with sessions.begin() as db:
            user = db.scalar(select(User).where(User.id == tenant.user_id).with_for_update())
            if user.access_hash != digest(body.access_code):
                raise HTTPException(403, "Confirm your current personal access code")
            result = rotate_credentials(db, user)
        response.delete_cookie("ml_session")
        return result

    @app.get("/api/integrations")
    def integration_list(tenant=Depends(personal)):
        with sessions() as db:
            providers = list(
                db.scalars(
                    select(IntegrationConnection.provider).where(
                        IntegrationConnection.household_id == tenant.id,
                        IntegrationConnection.user_id == tenant.user_id,
                    )
                )
            )
            return {"connected": providers}

    @app.delete("/api/integrations/google")
    def disconnect_google(tenant=Depends(personal)):
        from sqlalchemy import delete

        from mental_loader.storage import OAuthAttempt

        with sessions.begin() as db:
            db.execute(
                delete(IntegrationConnection).where(
                    IntegrationConnection.household_id == tenant.id,
                    IntegrationConnection.user_id == tenant.user_id,
                    IntegrationConnection.provider == "google-calendar",
                )
            )
            db.execute(
                delete(OAuthAttempt).where(
                    OAuthAttempt.household_id == tenant.id,
                    OAuthAttempt.user_id == tenant.user_id,
                )
            )
        return {"ok": True}

    @app.post("/api/integrations/google/connect")
    def connect_google(request: Request, tenant=Depends(personal)):
        from mental_loader.integrations import begin_google

        quota(f"google-connect:{tenant.user_id}", 5, 600)
        try:
            url = begin_google(
                sessions, settings, tenant, digest(request.cookies.get("ml_session", ""))
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                503, "Configure the private app key and Google web OAuth client"
            ) from error
        return {"authorization_url": url}

    @app.get("/api/integrations/google/callback")
    def google_callback(request: Request, state: str, code: str, tenant=Depends(personal)):
        from mental_loader.integrations import finish_google

        quota(f"google-callback:{tenant.user_id}", 10, 600)
        try:
            finish_google(
                sessions,
                settings,
                tenant,
                digest(request.cookies.get("ml_session", "")),
                state,
                code,
            )
        except ValueError as error:
            raise HTTPException(
                400, "Calendar authorization expired or was not granted; reconnect"
            ) from error
        except Exception as error:
            raise HTTPException(
                502, "Calendar authorization failed; reconnect to try again"
            ) from error
        return RedirectResponse("/", status_code=303)

    @app.get("/api/integrations/google/events")
    def google_events(tenant=Depends(personal)):
        from mental_loader.integrations import calendar_events

        quota(f"calendar-read:{tenant.user_id}", 10)
        try:
            return {"events": calendar_events(sessions, settings, tenant.id, tenant.user_id)}
        except LookupError as error:
            raise HTTPException(404, "Connect your Google Calendar first") from error
        except Exception as error:
            raise HTTPException(
                502, "Calendar is unavailable; reconnect if access was revoked"
            ) from error

    @app.get("/api/usage")
    def usage(tenant=Depends(personal)):
        with sessions() as db:
            rows = list(
                db.scalars(
                    select(AIUsage).where(
                        AIUsage.household_id == tenant.id,
                        AIUsage.user_id == tenant.user_id,
                    )
                )
            )
            return {
                "requests": len(rows),
                "input_tokens": sum(r.input_tokens for r in rows),
                "output_tokens": sum(r.output_tokens for r in rows),
            }

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        with sessions.begin() as db:
            from sqlalchemy import delete

            device = request.cookies.get("ml_device", "")
            if device:
                db.execute(
                    delete(PushSubscription).where(PushSubscription.device_hash == digest(device))
                )
            login = db.get(LoginSession, digest(request.cookies.get("ml_session", "")))
            if login:
                db.delete(login)
        response.delete_cookie("ml_session")
        return {"ok": True}

    @app.get("/api/me")
    def me(tenant=Depends(household)):
        return {
            "household": tenant.name,
            "household_id": tenant.id,
            "user_id": tenant.user_id,
            "role": tenant.role,
            "conversation_visibility": "household",
            "timezone": tenant.timezone,
            "ai_enabled": settings.ai_enabled,
            "push_configured": bool(settings.vapid_public_key),
            "vapid_public_key": settings.vapid_public_key,
            "calendar": "Google Calendar — OAuth probe via CLI; sync not enabled",
            "google_available": (
                settings.google_web_client_path.is_file() and settings.secret_key_path.is_file()
            ),
        }

    def owner(tenant=Depends(household)):
        if tenant.role != "owner":
            raise HTTPException(403, "Household owner access required")
        return tenant

    @app.get("/api/members")
    def members(tenant=Depends(owner)):
        with sessions() as db:
            rows = db.execute(
                select(User.id, User.name, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.household_id == tenant.id)
            )
            return [dict(row._mapping) for row in rows]

    @app.delete("/api/members/{user_id}")
    def revoke_membership(user_id: str, tenant=Depends(owner)):
        with sessions.begin() as db:
            try:
                remove_member(db, tenant.id, user_id)
            except ValueError as error:
                status = 409 if "last owner" in str(error) else 404
                raise HTTPException(status, str(error)) from error
        return {"ok": True}

    @app.get("/api/conversations")
    def conversations(tenant=Depends(household)):
        with sessions() as db:
            rows = db.scalars(
                select(Conversation)
                .where(visible_conversations(tenant.id, tenant.user_id))
                .order_by(Conversation.created_at.desc())
            )
            return [
                {"id": row.id, "created_at": row.created_at, "visibility": row.visibility}
                for row in rows
            ]

    @app.post("/api/conversations")
    def new_conversation(body: ConversationInput | None = None, tenant=Depends(household)):
        visibility = body.visibility if body else "household"
        if visibility == "private" and not tenant.user_id:
            raise HTTPException(403, "Personal sign-in is required for private chats")
        with sessions.begin() as db:
            row = Conversation(
                household_id=tenant.id, visibility=visibility, owner_id=tenant.user_id
            )
            db.add(row)
            db.flush()
            if visibility == "private":
                db.add(
                    ConversationParticipant(
                        household_id=tenant.id, conversation_id=row.id, user_id=tenant.user_id
                    )
                )
            return {"id": row.id}

    def managed_conversation(db, tenant, conversation_id):
        row = conversation_for(db, tenant.id, conversation_id, tenant.user_id)
        if row.visibility != "private" or row.owner_id != tenant.user_id:
            raise HTTPException(403, "Only the private chat creator can manage participants")
        return row

    @app.get("/api/conversations/{conversation_id}/participants")
    def participants(conversation_id: str, tenant=Depends(household)):
        with sessions() as db:
            conversation = conversation_for(db, tenant.id, conversation_id, tenant.user_id)
            rows = db.execute(
                select(User.id, User.name)
                .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
                .where(
                    ConversationParticipant.household_id == tenant.id,
                    ConversationParticipant.conversation_id == conversation_id,
                )
            )
            return [
                {"user_id": row.id, "name": row.name, "owner": row.id == conversation.owner_id}
                for row in rows
            ]

    @app.get("/api/conversations/{conversation_id}/eligible-participants")
    def eligible_participants(conversation_id: str, tenant=Depends(household)):
        with sessions() as db:
            managed_conversation(db, tenant, conversation_id)
            rows = db.execute(
                select(User.id, User.name)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.household_id == tenant.id)
            )
            return [{"user_id": row.id, "name": row.name} for row in rows]

    @app.post("/api/conversations/{conversation_id}/participants")
    def add_participant(conversation_id: str, body: ParticipantInput, tenant=Depends(household)):
        with sessions.begin() as db:
            managed_conversation(db, tenant, conversation_id)
            user_id = str(body.user_id)
            if db.get(Membership, (tenant.id, user_id)) is None:
                raise HTTPException(404, "Household member not found")
            if db.get(ConversationParticipant, (conversation_id, user_id)) is None:
                db.add(
                    ConversationParticipant(
                        household_id=tenant.id, conversation_id=conversation_id, user_id=user_id
                    )
                )
        return {"ok": True}

    @app.delete("/api/conversations/{conversation_id}/participants/{user_id}")
    def remove_participant(conversation_id: str, user_id: str, tenant=Depends(household)):
        with sessions.begin() as db:
            row = managed_conversation(db, tenant, conversation_id)
            if user_id == row.owner_id:
                raise HTTPException(409, "The chat creator must remain a participant")
            participant = db.get(ConversationParticipant, (conversation_id, user_id))
            if participant is None:
                raise HTTPException(404, "Participant not found")
            db.delete(participant)
        return {"ok": True}

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str, tenant=Depends(household)):
        with sessions() as db:
            conversation = conversation_for(db, tenant.id, conversation_id, tenant.user_id)
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
                "visibility": conversation.visibility,
                "ai_available": bool(
                    settings.ai_enabled
                    and (
                        db.get(
                            RuntimeBinding,
                            (
                                f"conversation:{conversation.id}"
                                if conversation.visibility == "private"
                                else f"household:{tenant.id}"
                            ),
                        )
                        or (
                            conversation.visibility == "household"
                            and (not settings.hosted_mode or settings.paas_personal_runtime)
                            and settings.hermes_household_id == tenant.id
                        )
                    )
                ),
                "can_manage_participants": (
                    conversation.visibility == "private" and conversation.owner_id == tenant.user_id
                ),
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
        quota(f"message:{tenant.id}:{tenant.user_id}", 30)
        try:
            with sessions.begin() as db:
                conversation_for(db, tenant.id, conversation_id, tenant.user_id)
                from sqlalchemy import update

                db.execute(
                    update(Household).where(Household.id == tenant.id).values(name=Household.name)
                )
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
                    requested_by=tenant.user_id,
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
        return operations.tasks(tenant)

    @app.post("/api/tasks/{task_id}/complete")
    @app.post("/api/tool/tasks/{task_id}/complete")
    def complete(task_id: UUID, request: Request):
        return operations.complete_task(action_principal(request), str(task_id))

    @app.post("/api/reminders")
    def remind(body: ReminderInput, tenant=Depends(household)):
        return operations.remind(tenant, body)

    @app.post("/api/push-subscriptions")
    def subscribe(body: SubscriptionInput, request: Request, tenant=Depends(household)):
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
            if row and row.user_id != tenant.user_id:
                # An enrolled member can reclaim their existing legacy browser
                # subscription only by presenting the identical endpoint and keys.
                if row.user_id is not None or row.subscription != body.model_dump():
                    raise HTTPException(409, "Unsubscribe this browser before switching accounts")
            if row is None:
                row = PushSubscription(household_id=tenant.id, endpoint_hash=digest(body.endpoint))
                db.add(row)
            row.user_id = tenant.user_id
            row.session_hash = digest(request.cookies.get("ml_session", ""))
            device = request.cookies.get("ml_device", "")
            row.device_hash = digest(device) if device else None
            row.subscription = body.model_dump()
        return {"ok": True}

    @app.delete("/api/push-subscriptions")
    def unsubscribe(body: SubscriptionInput, tenant=Depends(household)):
        with sessions.begin() as db:
            row = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.endpoint_hash == digest(body.endpoint),
                    PushSubscription.household_id == tenant.id,
                    PushSubscription.user_id == tenant.user_id,
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
