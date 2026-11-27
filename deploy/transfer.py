"""Private setup files and stopped-service snapshots; never print credential values."""

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def private_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)


def export_env(destination, platform="railway"):
    from dotenv import dotenv_values

    from mental_loader.config import Settings

    settings = Settings()
    household = json.loads((ROOT / ".local/access-codes.json").read_text())["My household"]
    provider = dotenv_values(ROOT / ".local/hermes-home/.env").get("OPENROUTER_API_KEY")
    if not provider:
        raise RuntimeError("Configure the existing OpenRouter key before exporting")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    app_key = settings.secret_key_path
    bootstrap_key = ROOT / ".local/deployment-app-secret.key"
    if app_key.is_file():
        encoded_key = app_key.read_text().strip()
    elif bootstrap_key.is_file():
        encoded_key = bootstrap_key.read_text().strip()
    else:
        # Preserve the key prepared for the earlier, undeployed Render setup.
        previous = dotenv_values(ROOT / ".local/render-phase2-inputs/web.env")
        encoded_key = previous.get("APP_SECRET_KEY_B64") or base64.urlsafe_b64encode(
            os.urandom(32)
        ).decode()
        private_file(bootstrap_key, encoded_key + "\n")
    if len(base64.urlsafe_b64decode(encoded_key)) != 32:
        raise RuntimeError("The existing deployment app key is invalid; preserve it for review")
    web = {
        "DEPLOYMENT_READY": "false",
        "HERMES_HOUSEHOLD_ID": household["household_id"],
        "VAPID_PRIVATE_KEY_B64": base64.b64encode(
            settings.vapid_private_key_path.read_bytes()
        ).decode(),
        "VAPID_PUBLIC_KEY": settings.vapid_public_key,
        "VAPID_SUBJECT": settings.vapid_subject,
        "APP_SECRET_KEY_B64": encoded_key,
    }
    hermes = {
        "DEPLOYMENT_READY": "false",
        "ML_BACKEND_URL": "https://configure-after-first-deploy.invalid",
        "OPENROUTER_API_KEY": provider,
    }
    if platform == "railway":
        shared = {
            key: value for key, value in web.items() if key != "DEPLOYMENT_READY"
        }
        shared.update({
            "OPENROUTER_API_KEY": provider,
            "HERMES_API_KEY": secrets.token_urlsafe(32),
            "HERMES_TOOL_TOKEN": secrets.token_urlsafe(32),
            "WEB_ORIGIN": "https://configure-after-first-deploy.invalid",
            "WEB_READY": "false",
            "HERMES_READY": "false",
        })
        outputs = (("shared.env", shared),)
    else:
        outputs = (("web.env", web), ("hermes.env", hermes))
    for name, values in outputs:
        private_file(destination / name, "".join(
            f"{key}={json.dumps(value)}\n" for key, value in values.items()
        ))
    print(f"Private {platform} inputs saved in {destination}; import in its dashboard, not chat.")


def snapshot(destination):
    for port in (8000, 8642):
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Stop local app and Hermes before taking the final snapshot")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    with (destination / "database.dump").open("xb") as stream:
        os.chmod(stream.name, 0o600)
        subprocess.run([
            "docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "mental_loader",
            "-d", "mental_loader", "--format=custom", "--no-owner", "--no-acl",
        ], cwd=ROOT, stdout=stream, check=True)
    source = ROOT / ".local/hermes-home"
    archive_path = destination / "hermes-home.tar.gz"
    descriptor = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with (
        os.fdopen(descriptor, "wb") as output,
        tarfile.open(fileobj=output, mode="w:gz") as archive,
    ):
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in {"logs", "__pycache__", ".git", ".env", ".state-imported"}
                   for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file() or path.suffix in {".lock", ".pid", ".sock"}:
                continue
            archive.add(path, arcname=str(relative), recursive=False)
    print(f"Stopped-service snapshot saved in {destination}. Keep local services stopped.")


def require_setup():
    if os.environ.get("DEPLOYMENT_READY", "false").lower() == "true":
        raise RuntimeError("Set DEPLOYMENT_READY=false and deploy before restoring state")


def restore_home(archive_path, destination):
    require_setup()
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("Restore requires an empty Hermes home; preserve any existing state")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError("Archive must contain only relative regular files/directories")
            if member.isfile():
                member.mode = 0o600
            else:
                member.mode = 0o700
        archive.extractall(destination, members=members, filter="data")
    if not (destination / "config.yaml").is_file():
        raise RuntimeError("Snapshot lacks Hermes config.yaml")
    private_file(destination / ".state-imported", "Restored while scheduler was stopped.\n")
    print("Hermes state restored. Scheduler remains stopped until DEPLOYMENT_READY=true.")


def restore_database(path):
    require_setup()
    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    # Supply the password through the process environment, never a command argument.
    pg_env = dict(os.environ, PGHOST=url.host or "", PGPORT=str(url.port or 5432),
                  PGUSER=url.username or "", PGPASSWORD=url.password or "",
                  PGDATABASE=url.database or "")
    if url.query.get("sslmode"):
        pg_env["PGSSLMODE"] = url.query["sslmode"]
    with psycopg.connect(
        host=pg_env["PGHOST"], port=pg_env["PGPORT"], user=pg_env["PGUSER"],
        password=pg_env["PGPASSWORD"], dbname=pg_env["PGDATABASE"],
        **({"sslmode": pg_env["PGSSLMODE"]} if "PGSSLMODE" in pg_env else {}),
    ) as connection:
        count = connection.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        ).fetchone()[0]
        if count:
            raise RuntimeError("Restore requires an empty database; existing data was preserved")
    subprocess.run([
        "pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
        "--dbname", url.database, str(path),
    ], env=pg_env, check=True)
    print("Database restored. Web worker remains stopped until DEPLOYMENT_READY=true.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export-env")
    export.add_argument("destination", type=Path)
    export.add_argument("--platform", choices=("railway", "render"), default="railway")
    snapshot_command = sub.add_parser("snapshot")
    snapshot_command.add_argument("destination", type=Path)
    home = sub.add_parser("restore-home")
    home.add_argument("archive", type=Path)
    home.add_argument("--destination", type=Path, default=Path("/data/hermes-home"))
    db = sub.add_parser("restore-db")
    db.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.command == "export-env":
        export_env(args.destination, args.platform)
    elif args.command == "snapshot":
        snapshot(args.destination)
    elif args.command == "restore-home":
        restore_home(args.archive, args.destination)
    else:
        restore_database(args.archive)


if __name__ == "__main__":
    main()
