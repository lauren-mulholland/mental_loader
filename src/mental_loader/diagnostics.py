"""Read-only local readiness checks. Never print credentials or issue model requests."""

import json
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from mental_loader.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def readiness(settings=None):
    settings = settings or Settings()
    checks = []

    def record(name, ready, action):
        checks.append({"check": name, "ready": bool(ready), "action": "Ready" if ready else action})

    record("Application environment", (ROOT / ".venv/bin/python").exists(), "Run uv sync --locked")
    record(
        "Hermes environment",
        (ROOT / ".local/hermes-runtime/.venv/bin/python").exists(),
        "Install the pinned Hermes runtime with its messaging extra; see docs/local-setup.md",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": 3}
        if settings.database_url.startswith("postgresql")
        else {},
    )
    try:
        with engine.connect() as db:
            db.execute(text("select 1"))
        record("Database", True, "")
    except Exception:
        record("Database", False, "Start Docker Desktop, then docker compose up -d db")
    finally:
        engine.dispose()
    try:
        with httpx.Client(timeout=3, follow_redirects=False) as client:
            result = client.get(settings.public_origin.rstrip("/") + "/health")
        record(
            "Application HTTP",
            result.status_code == 200,
            "Run uv run python -m mental_loader.cli serve",
        )
    except httpx.HTTPError:
        record("Application HTTP", False, "Run uv run python -m mental_loader.cli serve")
    try:
        with httpx.Client(timeout=3, follow_redirects=False) as client:
            result = client.get(
                settings.hermes_base_url.rstrip("/") + "/v1/capabilities",
                headers={"Authorization": f"Bearer {settings.hermes_api_key}"},
            )
        record(
            "Hermes API",
            result.status_code == 200,
            "Run bash scripts/hermes.sh gateway run; check its messaging dependencies and API key",
        )
    except httpx.HTTPError:
        record("Hermes API", False, "Run bash scripts/hermes.sh gateway run")

    record(
        "PWA chat enabled",
        settings.ai_enabled,
        "After configuring a provider, set AI_ENABLED=true in .env and restart the app",
    )
    subject_ready = (
        settings.vapid_subject.startswith("mailto:") and "replace-me" not in settings.vapid_subject
    )
    record(
        "Push sender configured",
        settings.vapid_public_key and settings.vapid_private_key_path.exists() and subject_ready,
        "Run init and set VAPID_SUBJECT=mailto:your-email in .env; restart the app",
    )
    record(
        "Google OAuth client",
        settings.google_client_secret_path.exists(),
        "Later: create a Google Desktop OAuth client and save .local/google-client.json",
    )

    home = ROOT / ".local/hermes-home"
    env = dotenv_values(home / ".env") if (home / ".env").exists() else {}
    provider_names = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOUS_API_KEY")
    has_key = any(bool(env.get(name)) for name in provider_names)
    has_auth_file = (home / "auth.json").exists()
    record(
        "Provider credential present (not validated)",
        has_key or has_auth_file,
        "Use bash scripts/hermes.sh model to connect a provider locally; do not paste keys in chat",
    )
    native = ROOT / "scripts/hermes_status.py"
    python = ROOT / ".local/hermes-runtime/.venv/bin/python"
    if python.exists() and native.exists():
        try:
            completed = subprocess.run(
                [str(python), str(native)], capture_output=True, text=True, timeout=15, check=True
            )
            status = json.loads(completed.stdout)
            record(
                "Native reminder job registered",
                status["job_registered"],
                "Run uv run python -m mental_loader.cli setup-hermes",
            )
            record(
                "Hermes scheduler heartbeat",
                status["ticker_fresh"],
                "Start the Hermes gateway; job registration alone does not start scheduling",
            )
        except (subprocess.SubprocessError, ValueError, KeyError):
            record("Native scheduler status", False, "Check the private Hermes runtime setup")
    return checks


def main():
    for row in readiness():
        print(f"{'OK' if row['ready'] else 'SETUP'}  {row['check']}: {row['action']}")
    print("No provider requests made. Credential presence does not prove authentication works.")


if __name__ == "__main__":
    main()
