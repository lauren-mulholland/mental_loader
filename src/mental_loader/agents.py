"""Thin Hermes adapter; required household actions remain ordinary Python."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, update

from mental_loader.domain import create_reminder
from mental_loader.permissions import can_access
from mental_loader.runtimes import resolve, scope_id
from mental_loader.storage import AIUsage, Conversation, Household, Run, RuntimeBinding, now

PERSONA = Path(__file__).parent / "hermes_instructions.md"


class HermesResponseError(RuntimeError):
    """Only a safe category crosses into application logs; never raw provider errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class HouseholdAgent:
    def __init__(self, sessions, settings, respond=None):
        self.sessions, self.settings, self.respond = sessions, settings, respond

    def invoke(self, state):
        settings = self.settings
        text = state["text"].strip()
        with self.sessions.begin() as db:
            run = db.scalar(
                select(Run).where(
                    Run.id == state["run_id"], Run.household_id == state["household_id"]
                )
            )
            if run is None:
                raise ValueError("Run not found")
            text = run.input.strip()
            if state.get("claim_token") and run.claim_token != state["claim_token"]:
                raise HermesResponseError("stale_worker")
            if state.get("claim_token") and (
                run.status != "running"
                or run.lease_until is None
                or (
                    run.lease_until.replace(tzinfo=UTC)
                    if run.lease_until.tzinfo is None
                    else run.lease_until
                )
                <= now()
            ):
                raise HermesResponseError("expired_worker")
            if not can_access(db, run.household_id, run.conversation_id, run.requested_by):
                raise HermesResponseError("conversation_access_revoked")
            household = db.get(Household, run.household_id)
            if text.startswith("/remind"):
                try:
                    task = create_reminder(db, run, household.timezone, now())
                except ValueError as error:
                    return {"reply": str(error)}
                local = task.due_at.astimezone(ZoneInfo(household.timezone))
                return {"reply": f"Saved: {task.title}. Due {local:%d %b %Y, %H:%M %Z}."}
            conversation_row = db.get(Conversation, run.conversation_id)
            binding = db.get(
                RuntimeBinding,
                scope_id(
                    run.household_id,
                    run.conversation_id if conversation_row.visibility == "private" else None,
                ),
            )
            if conversation_row.visibility != "household" and binding is None:
                return {
                    "reply": "Private chats support /remind commands. Natural-language AI "
                    "is unavailable here until a separate private runtime is validated."
                }
            if not settings.ai_enabled:
                return {
                    "reply": "Hermes chat is off. /remind 1m Buy a birthday card works "
                    "without model credits. Configure Hermes and AI_ENABLED to chat naturally."
                }
            route = resolve(db, settings, run.household_id, conversation_row) if binding else None
            if route is None and (
                (settings.hosted_mode and not settings.paas_personal_runtime)
                or settings.hermes_household_id != run.household_id
            ):
                return {
                    "reply": "This household has no isolated Hermes runtime configured. "
                    "Tasks can be saved, but automatic delivery needs "
                    "this household's Hermes scheduler."
                }
            endpoint, api_key, runtime_scope = route or (
                settings.hermes_base_url,
                settings.hermes_api_key,
                scope_id(run.household_id),
            )
            if not api_key and self.respond is None:
                return {"reply": "Set HERMES_API_KEY after configuring your local Hermes gateway."}
            midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            # Serialize budget reservations across workers, including SQLite's
            # lack of row-level FOR UPDATE. No provider call occurs in this transaction.
            db.execute(
                update(Household)
                .where(Household.id == run.household_id)
                .values(name=Household.name)
            )
            count = db.scalar(
                select(func.count())
                .select_from(AIUsage)
                .where(AIUsage.household_id == run.household_id, AIUsage.created_at >= midnight)
            )
            if count >= settings.ai_daily_call_limit:
                return {"reply": "Daily Hermes request limit reached. Your reminders still work."}
            usage = AIUsage(household_id=run.household_id, run_id=run.id, user_id=run.requested_by)
            db.add(usage)
            db.flush()
            usage_id = usage.id
            context = (
                f"Application conversation_id: {run.conversation_id}. "
                f"Household timezone: {household.timezone}. Use household_clock for now."
            )
            conversation = f"mental-loader:{run.household_id}:{run.conversation_id}"
        prompt = text
        if self.respond:
            return {"reply": self.respond(prompt)}
        # Named conversations retain Hermes' own tool/session history across reloads.
        # No automatic retry: an uncertain execution may already have changed tasks.
        with httpx.Client(
            base_url=endpoint.rstrip("/"), timeout=120, follow_redirects=False
        ) as client:
            response = client.post(
                "/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Idempotency-Key": state["run_id"],
                    # A new app chat must start a fresh native session/memory snapshot.
                    # Profile memory remains shared within the dedicated household home.
                    "X-Hermes-Session-Key": conversation,
                },
                json={
                    "model": "hermes-agent",
                    "conversation": conversation,
                    "instructions": PERSONA.read_text() + "\n" + context,
                    "input": prompt,
                },
            )
            response.raise_for_status()
            payload = response.json()
        with self.sessions.begin() as db:
            usage = db.get(AIUsage, usage_id)
            usage.input_tokens = payload.get("usage", {}).get("input_tokens", 0)
            usage.output_tokens = payload.get("usage", {}).get("output_tokens", 0)
        if payload.get("status") != "completed" or payload.get("error"):
            code = (
                "invalid_encrypted_content"
                if "invalid_encrypted_content" in str(payload.get("error", ""))
                else "hermes_turn_failed"
            )
            raise HermesResponseError(code)
        reply = "\n".join(
            part.get("text", "")
            for item in payload.get("output", [])
            if item.get("type") == "message" and item.get("phase") != "commentary"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        if not reply:
            raise RuntimeError("Hermes returned no final text")
        return {"reply": reply}


def build_agent(sessions, settings, respond=None):
    return HouseholdAgent(sessions, settings, respond)
