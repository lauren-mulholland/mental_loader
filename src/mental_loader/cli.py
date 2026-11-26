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


def google_probe(household_id: str, user_id: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    from mental_loader.integrations import save_connection, scope
    from mental_loader.security import Vault
    from mental_loader.storage import IntegrationConnection, Membership

    settings = Settings()
    engine, sessions = database(settings.database_url)
    vault = Vault(settings.secret_key_path)
    with sessions() as db:
        if not db.get(Membership, (household_id, user_id)):
            raise SystemExit("Choose a personal account with an active household membership.")
        row = db.get(IntegrationConnection, (household_id, user_id, "google-calendar"))
        saved = vault.open(row.encrypted_credentials, scope(household_id, user_id)) if row else None
    scopes = ["https://www.googleapis.com/auth/calendar.events.readonly"]
    credentials = None
    if saved:
        credentials = Credentials.from_authorized_user_info(saved, scopes)
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
        save_connection(
            sessions, settings, household_id, user_id, json.loads(credentials.to_json())
        )
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
    google.add_argument("--user-id", required=True)
    sub.add_parser("serve", help="Run the local feasibility app on localhost:8000")
    sub.add_parser("setup-hermes", help="Prepare a private Hermes home and native plugin")
    sub.add_parser("doctor", help="Check local services and setup without model calls or secrets")
    sub.add_parser("migrate", help="Apply forward-only database migrations without model calls")
    account = sub.add_parser("create-account", help="Provision a personal local sign-in code")
    account.add_argument("--household-id", required=True)
    account.add_argument("--name", required=True)
    account.add_argument("--role", choices=("owner", "member"), required=True)
    account.add_argument("--output", type=Path, required=True, help="New private credentials file")
    sub.add_parser("init-secrets", help="Create a private app encryption key if absent")
    reset = sub.add_parser(
        "reset-account", help="Operator recovery; revoke sessions and device delivery"
    )
    reset.add_argument("--user-id", required=True)
    reset.add_argument("--output", type=Path, required=True)
    member = sub.add_parser("add-member", help="Grant or change an existing account membership")
    member.add_argument("--household-id", required=True)
    member.add_argument("--user-id", required=True)
    member.add_argument("--role", required=True, choices=("owner", "member"))
    register = sub.add_parser("register-runtime", help="Attest and register a scoped container")
    register.add_argument("--manifest", type=Path, required=True)
    revoke = sub.add_parser(
        "revoke-runtime", help="Remove a route and invalidate its tool credential"
    )
    revoke.add_argument("--scope", required=True)
    backup = sub.add_parser("backup", help="Create an encrypted snapshot; quiesce Hermes first")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--home", type=Path, action="append", default=[])
    backup.add_argument("--runtime-manifest", type=Path, action="append", default=[])
    restore = sub.add_parser("restore", help="Restore to empty RESTORE_DATABASE_URL and new homes")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--homes-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialise()
    elif args.command == "google-probe":
        google_probe(args.household_id, args.user_id)
    elif args.command == "setup-hermes":
        setup_hermes()
    elif args.command == "doctor":
        from mental_loader.diagnostics import main as diagnostics

        diagnostics()
    elif args.command == "migrate":
        engine, _ = database(Settings().database_url)
        engine.dispose()
        print("Database migrations applied.")
    elif args.command == "init-secrets":
        from mental_loader.security import Vault

        path = Settings().secret_key_path
        if not path.exists():
            write_private(path, base64.urlsafe_b64encode(os.urandom(32)).decode() + "\n")
        Vault(path)
        print("Private app key ready; preserve it separately from encrypted backups.")
    elif args.command == "reset-account":
        from mental_loader.accounts import rotate_credentials
        from mental_loader.storage import User

        if args.output.exists():
            raise SystemExit("Output already exists; choose a new private path")
        engine, sessions = database(Settings().database_url)
        try:
            with sessions.begin() as db:
                user = db.scalar(select(User).where(User.id == args.user_id).with_for_update())
                if user is None:
                    raise SystemExit("Unknown account")
                result = rotate_credentials(db, user)
                write_private(args.output, json.dumps(result, indent=2) + "\n")
        finally:
            engine.dispose()
        print("Account credentials rotated; sessions and device registrations revoked.")
    elif args.command == "add-member":
        from mental_loader.accounts import grant_membership

        engine, sessions = database(Settings().database_url)
        try:
            with sessions.begin() as db:
                grant_membership(db, args.household_id, args.user_id, args.role)
        finally:
            engine.dispose()
        print("Account membership updated; private conversation access is unchanged.")
    elif args.command in {"register-runtime", "revoke-runtime"}:
        from sqlalchemy import delete

        from mental_loader.runtimes import register
        from mental_loader.storage import RuntimeBinding

        settings = Settings()
        engine, sessions = database(settings.database_url)
        try:
            if args.command == "register-runtime":
                manifest = json.loads(args.manifest.read_text())
                register(
                    sessions,
                    settings,
                    **{
                        key: manifest[key]
                        for key in (
                            "household_id",
                            "conversation_id",
                            "container_id",
                            "endpoint",
                            "api_key",
                            "tool_code",
                        )
                    },
                )
            else:
                with sessions.begin() as db:
                    db.execute(delete(RuntimeBinding).where(RuntimeBinding.scope == args.scope))
        finally:
            engine.dispose()
        print("Runtime route updated; no model calls made.")
    elif args.command in {"backup", "restore"}:
        from mental_loader.backups import create_backup, export_runtime_home, restore_backup

        settings = Settings()
        if args.command == "backup":
            import tempfile

            engine, sessions = database(settings.database_url)
            try:
                with tempfile.TemporaryDirectory(prefix="ml-export-") as temporary:
                    homes = list(args.home)
                    for index, manifest in enumerate(args.runtime_manifest):
                        homes.append(
                            export_runtime_home(manifest, Path(temporary) / f"runtime-{index}")
                        )
                    create_backup(sessions, settings, args.output, homes)
            finally:
                engine.dispose()
        else:
            target = os.environ.get("RESTORE_DATABASE_URL")
            if not target:
                raise SystemExit("Set RESTORE_DATABASE_URL to a fresh destination database")
            restore_backup(settings, args.input, target, args.homes_output)
        print("Encrypted backup operation completed; no existing database was overwritten.")
    elif args.command == "create-account":
        from mental_loader.accounts import provision_account

        # Refuse an existing output before touching account state. Exclusive file
        # creation also protects against a race; a write failure rolls back enrollment.
        if args.output.exists():
            raise SystemExit("Output file already exists; choose a new private path.")
        engine, sessions = database(Settings().database_url)
        written = False
        try:
            with sessions.begin() as db:
                from mental_loader.accounts import recovery_code

                user, code = provision_account(db, args.household_id, args.name, args.role)
                recovery = recovery_code(db, user)
                write_private(
                    args.output,
                    json.dumps(
                        {
                            "user_id": user.id,
                            "household_id": args.household_id,
                            "name": user.name,
                            "access_code": code,
                            "recovery_code": recovery,
                        },
                        indent=2,
                    )
                    + "\n",
                )
                written = True
        except BaseException:
            if written:
                args.output.unlink()
            raise
        finally:
            engine.dispose()
        print("Personal account created. Credentials saved in the requested private file.")
    else:
        import uvicorn

        uvicorn.run(
            "mental_loader.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=8000,
            access_log=False,
        )


if __name__ == "__main__":
    main()
