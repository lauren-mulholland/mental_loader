import secrets

import pytest
from fastapi.testclient import TestClient

from mental_loader.app import create_app
from mental_loader.config import Settings
from mental_loader.storage import Household, digest


@pytest.fixture
def context(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/test.db")
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
