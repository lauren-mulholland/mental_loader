"""Encrypted restore drill with synthetic household records and native SQLite state."""

import base64
import os
import sqlite3

import pytest
from conftest import login
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from test_accounts import account
from test_feasibility import submit

from mental_loader.backups import create_backup, restore_backup
from mental_loader.integrations import save_connection, scope
from mental_loader.security import Vault
from mental_loader.storage import (
    IntegrationConnection,
    LoginSession,
    Message,
    Run,
    Task,
    database,
    now,
)
from mental_loader.worker import process_run


def test_encrypted_backup_restores_data_and_native_state_to_fresh_destination(
    context,
    tmp_path,
    restore_database_url,
):
    app, client, households, settings = context
    key = tmp_path / "key"
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    key.chmod(0o600)
    settings.secret_key_path = key
    user_id, code = account(app, households[0][0])
    login(client, code)
    submit(client, "/remind 1m Synthetic restore reminder")
    process_run(app.state.sessions, app.state.agent)
    _, uncertain = submit(client, "Synthetic unfinished turn")
    with app.state.sessions.begin() as db:
        row = db.get(Run, uncertain.json()["run_id"])
        row.status = "running"
        row.claim_token = "synthetic-claim"
        row.lease_until = now()
    save_connection(
        app.state.sessions,
        settings,
        households[0][0],
        user_id,
        {"refresh_token": "synthetic-secret"},
    )
    home = tmp_path / "synthetic-home"
    home.mkdir()
    (home / "MEMORY.md").write_text("Synthetic native preference")
    procedure = home / "procedure.sh"
    procedure.write_text("#!/bin/sh\nexit 0\n")
    procedure.chmod(0o700)
    with sqlite3.connect(home / "state.db") as db:
        db.execute("CREATE TABLE fixture (message TEXT)")
        db.execute("INSERT INTO fixture VALUES ('Synthetic session')")
    output = tmp_path / "snapshot.enc"
    create_backup(app.state.sessions, settings, output, [home])
    assert "Synthetic" not in output.read_text()
    assert "synthetic-secret" not in output.read_text()
    target = restore_database_url
    restored_homes = tmp_path / "restored-homes"
    restore_backup(settings, output, target, restored_homes)
    engine, sessions = database(target)
    try:
        with sessions() as db:
            assert db.scalar(select(Task)).title == "Synthetic restore reminder"
            assert len(list(db.scalars(select(Message)))) == 4
            assert db.get(Run, uncertain.json()["run_id"]).status == "interrupted"
            assert (
                db.scalar(
                    select(Message).where(
                        Message.content
                        == "Restored from backup; check tasks before submitting again."
                    )
                )
                is not None
            )
            assert list(db.scalars(select(LoginSession))) == []
            connection = db.scalar(select(IntegrationConnection))
            assert (
                Vault(key).open(connection.encrypted_credentials, scope(households[0][0], user_id))[
                    "refresh_token"
                ]
                == "synthetic-secret"
            )
        assert (
            restored_homes / home.name / "MEMORY.md"
        ).read_text() == "Synthetic native preference"
        assert (restored_homes / home.name / "procedure.sh").stat().st_mode & 0o100
        with sqlite3.connect(restored_homes / home.name / "state.db") as db:
            assert db.execute("SELECT message FROM fixture").fetchone()[0] == "Synthetic session"
        with pytest.raises(ValueError, match="empty destination"):
            restore_backup(settings, output, target, tmp_path / "another-homes")
        assert not (tmp_path / "another-homes").exists()
        tampered = tmp_path / "tampered.enc"
        text = output.read_text()
        tampered.write_text(("A" if text[0] != "A" else "B") + text[1:])
        with pytest.raises(InvalidTag):
            restore_backup(settings, tampered, target, tmp_path / "tampered-homes")
    finally:
        engine.dispose()
