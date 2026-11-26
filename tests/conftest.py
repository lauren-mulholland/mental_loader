import os
import secrets
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from mental_loader.app import create_app
from mental_loader.config import Settings
from mental_loader.storage import Household, digest


def isolated_database_url(tmp_path, filename):
    url = os.environ.get("MENTAL_LOADER_TEST_POSTGRES_URL")
    if not url:
        yield f"sqlite:///{tmp_path}/{filename}"
        return
    # A unique schema per test; never drop or reset a pre-existing database/schema.
    schema = "ml_test_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        yield (
            make_url(url)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


@pytest.fixture
def database_url(tmp_path):
    yield from isolated_database_url(tmp_path, "test.db")


@pytest.fixture
def restore_database_url(tmp_path):
    yield from isolated_database_url(tmp_path, "restored.db")


@pytest.fixture
def context(database_url):
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        ai_enabled=False,
        hermes_api_key="",
        hermes_household_id="",
        vapid_public_key="",
    )
    app = create_app(settings, start_worker=False)
    sessions = app.state.sessions
    households = []
    with sessions.begin() as db:
        for name in ("A", "B"):
            code = secrets.token_urlsafe(32)
            row = Household(name=name, access_hash=digest(code))
            db.add(row)
            db.flush()
            households.append((row.id, code))
    with TestClient(app) as client:
        client.headers["origin"] = "http://localhost:8000"
        yield app, client, households, settings


def login(client, code):
    response = client.post("/api/login", json={"access_code": code})
    assert response.status_code == 200
