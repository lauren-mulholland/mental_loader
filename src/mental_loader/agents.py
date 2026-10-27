"""Thin Hermes adapter; required household actions remain ordinary Python."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from mental_loader.domain import create_reminder
from mental_loader.storage import AIUsage, Household, Run, now

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
            household = db.get(Household, run.household_id)
            if text.startswith("/remind"):
                try:
                    task = create_reminder(db, run, household.timezone, now())
                except ValueError as error:
                    return {"reply": str(error)}
                local = task.due_at.astimezone(ZoneInfo(household.timezone))
                return {"reply": f"Saved: {task.title}. Due {local:%d %b %Y, %H:%M %Z}."}
            if not settings.ai_enabled:
                return {
                    "reply": "Hermes chat is off. /remind 1m Buy a birthday card works "
                    "without model credits. Configure Hermes and AI_ENABLED to chat naturally."
                }
            if settings.hermes_household_id != run.household_id:
                return {
                    "reply": "This household has no isolated Hermes runtime configured. "
                    "Tasks can be saved, but automatic delivery needs "
                    "this household's Hermes scheduler."
                }
            if not settings.hermes_api_key and self.respond is None:
                return {"reply": "Set HERMES_API_KEY after configuring your local Hermes gateway."}
            midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            count = db.scalar(
                select(func.count())
                .select_from(AIUsage)
                .where(AIUsage.household_id == run.household_id, AIUsage.created_at >= midnight)
            )
            if count >= settings.ai_daily_call_limit:
                return {"reply": "Daily Hermes request limit reached. Your reminders still work."}
            usage = AIUsage(household_id=run.household_id)
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
            base_url=settings.hermes_base_url.rstrip("/"), timeout=120, follow_redirects=False
        ) as client:
            response = client.post(
                "/v1/responses",
                headers={
                    "Authorization": f"Bearer {settings.hermes_api_key}",
                    "Idempotency-Key": state["run_id"],
                    "X-Hermes-Session-Key": f"mental-loader:household:{state['household_id']}",
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
