"""Encrypted portable snapshots and restore into a fresh database and fresh Hermes homes."""

import base64
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, create_engine, func, inspect, select, text

from mental_loader.migrations import REVISIONS
from mental_loader.security import Vault
from mental_loader.storage import Base, database


def create_backup(sessions, settings, destination, homes=()):
    destination = Path(destination)
    vault = Vault(settings.secret_key_path)
    if destination.exists():
        raise ValueError("Backup destination already exists")
    with sessions() as db:
        if db.bind.dialect.name == "postgresql":
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        tables = {}
        for table in Base.metadata.sorted_tables:
            rows = db.execute(select(table)).mappings()
            tables[table.name] = [
                {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in row.items()
                }
                for row in rows
            ]
    files = {}
    for raw_home in homes:
        home = Path(raw_home).resolve()
        if not home.is_dir() or home.name in files:
            raise ValueError("Each Hermes home must exist and have a unique basename")
        entries = {}
        for path in home.rglob("*"):
            if path.is_symlink():
                raise ValueError("Backup refuses symlinks in Hermes homes")
            if not path.is_file() or path.name.endswith(("-wal", "-shm")):
                continue
            if path.suffix in {".db", ".sqlite", ".sqlite3"}:
                # Include committed WAL state without copying a torn SQLite file.
                with tempfile.TemporaryDirectory(prefix="ml-sqlite-backup-") as temporary:
                    snapshot = Path(temporary) / "state.db"
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
                        with sqlite3.connect(snapshot) as target:
                            source.backup(target)
                    content = snapshot.read_bytes()
            else:
                content = path.read_bytes()
            entries[path.relative_to(home).as_posix()] = {
                "data": base64.b64encode(content).decode(),
                "executable": bool(path.stat().st_mode & stat.S_IXUSR),
            }
        files[home.name] = entries
    payload = {
        "version": 1,
        "revisions": [key for key, _ in REVISIONS],
        "tables": tables,
        "homes": files,
    }
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(vault.seal(payload, "mental-loader-backup:v1"))


def export_runtime_home(manifest_path, destination):
    """Export a stopped container's own home; live native state must be quiesced."""
    from mental_loader.runtimes import scope_id

    manifest = json.loads(Path(manifest_path).read_text())
    result = subprocess.run(
        ["docker", "inspect", manifest["container_id"]],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    item = json.loads(result.stdout)[0]
    scope = scope_id(manifest["household_id"], manifest["conversation_id"])
    if (
        item["Id"] != manifest["container_id"]
        or item["State"]["Running"]
        or item["Config"].get("Labels", {}).get("mental-loader.scope") != scope
    ):
        raise ValueError("Stop the matching scoped runtime before exporting its state")
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    subprocess.run(
        ["docker", "cp", manifest["container_id"] + ":/home/hermes/.", str(destination)],
        capture_output=True,
        check=True,
        timeout=120,
    )
    # Exported ownership comes from trusted daemon metadata, never a model-editable
    # marker. Restored containers refuse to import this home into another scope.
    marker = destination / ".mental-loader-scope.json"
    marker.write_text(json.dumps({"scope": scope}))
    marker.chmod(0o600)
    return destination


def restore_backup(settings, source, target_url, homes_output):
    payload = Vault(settings.secret_key_path).open(
        Path(source).read_text(), "mental-loader-backup:v1"
    )
    if payload.get("version") != 1 or payload.get("revisions") != [key for key, _ in REVISIONS]:
        raise ValueError("Restore requires the same schema revisions as the backup")
    homes_output = Path(homes_output)
    if homes_output.exists():
        raise ValueError("Restore homes destination must not exist")
    expected = set(Base.metadata.tables)
    if set(payload["tables"]) != expected:
        raise ValueError("Backup table manifest does not match the schema")
    # Validate all archive paths before creating any destination or changing the DB.
    for name, files in payload["homes"].items():
        for relative in [name, *files]:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("Invalid backup path")
    # Refuse populated destinations before running migrations against them.
    probe = create_engine(target_url, hide_parameters=True)
    try:
        with probe.connect() as connection:
            for name in inspect(connection).get_table_names():
                if name == "schema_revisions":
                    continue
                quoted = connection.dialect.identifier_preparer.quote(name)
                if connection.execute(text(f"SELECT 1 FROM {quoted} LIMIT 1")).first():
                    raise ValueError("Restore only accepts an empty destination database")
    finally:
        probe.dispose()
    engine, sessions = database(target_url)
    restored_homes = False
    try:
        with sessions.begin() as db:
            if any(
                db.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables
            ):
                raise ValueError("Restore only accepts an empty destination database")
            for table in Base.metadata.sorted_tables:
                if table.name in {
                    "login_sessions",
                    "oauth_attempts",
                    "runtime_bindings",
                    "push_subscriptions",
                }:
                    # Never revive sessions, devices or old runtime credentials.
                    continue
                rows = payload["tables"][table.name]
                for row in rows:
                    for column in table.columns:
                        if isinstance(column.type, DateTime) and row.get(column.name) is not None:
                            row[column.name] = datetime.fromisoformat(row[column.name])
                if rows:
                    db.execute(table.insert(), rows)
            # Use private staging files; a DB failure leaves no partially restored home.
            homes_output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                dir=homes_output.parent, prefix=".ml-restore-"
            ) as temp:
                staging = Path(temp) / "homes"
                staging.mkdir(mode=0o700)
                for name, files in payload["homes"].items():
                    for relative, saved in files.items():
                        content = saved["data"] if isinstance(saved, dict) else saved
                        executable = isinstance(saved, dict) and saved.get("executable", False)
                        path = staging / name / relative
                        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        descriptor = os.open(
                            path,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o700 if executable else 0o600,
                        )
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(base64.b64decode(content, validate=True))
                db.flush()
                staging.rename(homes_output)
                restored_homes = True
            # Do not automatically route restored runtimes to old container endpoints.
            from sqlalchemy import delete

            from mental_loader.storage import (
                Delivery,
                LoginSession,
                Message,
                OAuthAttempt,
                Run,
                RuntimeBinding,
            )

            db.execute(delete(RuntimeBinding))
            db.execute(delete(LoginSession))
            db.execute(delete(OAuthAttempt))
            from sqlalchemy import update

            for run in db.scalars(select(Run).where(Run.status == "running")):
                run.status, run.claim_token, run.lease_until = "interrupted", None, None
                run.result = "Restored from backup; check tasks before submitting again."
                db.add(
                    Message(
                        household_id=run.household_id,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        content=run.result,
                    )
                )
            db.execute(
                update(Delivery)
                .where(Delivery.status == "sending")
                .values(
                    status="pending",
                    claim_token=None,
                    lease_until=None,
                )
            )
    except BaseException:
        if restored_homes:
            shutil.rmtree(homes_output)
        raise
    finally:
        engine.dispose()
    return {"status": "restored", "runtime_routes": "revoked; re-register fresh containers"}
