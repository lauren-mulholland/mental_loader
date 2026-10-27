"""Local setup and account-dependent feasibility probes."""

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from mental_loader.config import Settings
from mental_loader.storage import Household, database, digest


def write_private(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)


def initialise():
    if not Path(".env").exists():
        write_private(Path(".env"), Path(".env.example").read_text())
    settings = Settings()
    settings.vapid_private_key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not settings.vapid_private_key_path.exists():
        private = ec.generate_private_key(ec.SECP256R1())
        write_private(
            settings.vapid_private_key_path,
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        )
    private = serialization.load_pem_private_key(
        settings.vapid_private_key_path.read_bytes(), password=None
    )
    public = (
        base64.urlsafe_b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        .rstrip(b"=")
        .decode()
    )
    env = Path(".env").read_text()
    if "VAPID_PUBLIC_KEY=\n" in env:
        Path(".env").write_text(env.replace("VAPID_PUBLIC_KEY=\n", f"VAPID_PUBLIC_KEY={public}\n"))
    engine, sessions = database(settings.database_url)
    codes_path = Path(".local/access-codes.json")
    if not codes_path.exists():
        codes = {}
        with sessions.begin() as db:
            for name in ("My household", "Isolation test household"):
                # Never rotate an existing household's code implicitly.
                if db.scalar(select(Household).where(Household.name == name)):
                    raise SystemExit(
                        "Households already exist; keep the original access-codes file."
                    )
                code = secrets.token_urlsafe(32)
                household = Household(name=name, access_hash=digest(code))
                db.add(household)
                db.flush()
                codes[name] = {"household_id": household.id, "access_code": code}
        write_private(codes_path, json.dumps(codes, indent=2) + "\n")
    engine.dispose()
    print("Setup ready. Household access codes: .local/access-codes.json (private local file).")
    print("Live AI is off unless AI_ENABLED=true. Set VAPID_SUBJECT to your email before push.")


def google_probe(household_id: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    settings = Settings()
    engine, sessions = database(settings.database_url)
    with sessions() as db:
        if not db.get(Household, household_id):
            raise SystemExit("Unknown household ID. Use .local/access-codes.json.")
    scopes = ["https://www.googleapis.com/auth/calendar.events.readonly"]
    token_path = Path(".local/google") / household_id / "token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not settings.google_client_secret_path.exists():
                raise SystemExit("Missing .local/google-client.json. Follow docs/feasibility.md.")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.google_client_secret_path), scopes
            )
            credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        if token_path.exists():
            token_path.write_text(credentials.to_json())
        else:
            write_private(token_path, credentials.to_json())
    with build("calendar", "v3", credentials=credentials, cache_discovery=False) as service:
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=datetime.now(UTC).isoformat(),
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    print(
        json.dumps(
            {
                "status": "connected",
                "household_id": household_id,
                "upcoming_events_read": len(result.get("items", [])),
                "scope": scopes[0],
                "writes_performed": False,
            },
            indent=2,
        )
    )
    engine.dispose()


def setup_hermes():
    """Use a repo-local Hermes home; never touch ~/.hermes or global Python."""
    root = Path(__file__).resolve().parents[2]
    home = root / ".local/hermes-home"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    codes = json.loads((root / ".local/access-codes.json").read_text())
    first = codes["My household"]
    secret_path = home / ".env"
    if not secret_path.exists():
        key = secrets.token_urlsafe(32)
        write_private(
            secret_path,
            "# Hermes model-provider credentials belong here, not in the root .env.\n"
            "# OPENROUTER_API_KEY=\n\n"
            "API_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\n"
            f"API_SERVER_PORT=8642\nAPI_SERVER_KEY={key}\n"
            "ML_BACKEND_URL=http://127.0.0.1:8000\n"
            f"ML_HOUSEHOLD_ACCESS_CODE={first['access_code']}\n",
        )
        with (root / ".env").open("a") as stream:
            stream.write(f"\nHERMES_API_KEY={key}\nHERMES_HOUSEHOLD_ID={first['household_id']}\n")
    config_path = home / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            "plugins:\n  enabled: [mental-loader]\n"
            "agent:\n  max_turns: 8\n"
            "delegation:\n  max_iterations: 4\n  max_concurrent_children: 1\n"
            "  max_spawn_depth: 1\n  inherit_mcp_toolsets: false\n"
            "platform_toolsets:\n"
            "  cli: [household, delegation, memory, session_search, skills, cronjob]\n"
            "  api_server: [household, delegation, memory, session_search, skills, cronjob]\n"
            "  cron: [household, memory, session_search, skills]\n"
            "skills:\n  creation_nudge_interval: 15\n"
        )
    plugin = home / "plugins/mental-loader"
    plugin.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(root / "hermes_plugin" / name, plugin / name)
    shutil.copyfile(root / "src/mental_loader/hermes_instructions.md", home / "SOUL.md")
    runtime_python = root / ".local/hermes-runtime/.venv/bin/python"
    if not runtime_python.exists():
        raise SystemExit("Install the isolated Hermes runtime first; see README.md.")
    subprocess.run([str(runtime_python), str(root / "scripts/patch_hermes.py")], check=True)
    subprocess.run([str(runtime_python), str(root / "scripts/configure_hermes.py")], check=True)
    print("Hermes home prepared; native memory, skills and cron enabled; no provider key added.")
    print("Use: bash scripts/hermes.sh setup (choose your provider/model), then gateway run.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Generate private local setup and two test households")
    google = sub.add_parser("google-probe", help="Read up to ten upcoming primary-calendar events")
    google.add_argument("--household-id", required=True)
    sub.add_parser("serve", help="Run the local feasibility app on localhost:8000")
    sub.add_parser("setup-hermes", help="Prepare a private Hermes home and native plugin")
    sub.add_parser("doctor", help="Check local services and setup without model calls or secrets")
    args = parser.parse_args()
    if args.command == "init":
        initialise()
    elif args.command == "google-probe":
        google_probe(args.household_id)
    elif args.command == "setup-hermes":
        setup_hermes()
    elif args.command == "doctor":
        from mental_loader.diagnostics import main as diagnostics

        diagnostics()
    else:
        import uvicorn

        uvicorn.run("mental_loader.app:create_app", factory=True, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
