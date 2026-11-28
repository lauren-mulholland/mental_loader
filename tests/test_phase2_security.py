"""Security regressions use synthetic accounts and mocked Google transport only."""

import base64
import json
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from test_accounts import account, subscription
from test_conversations import sign_in

from mental_loader import integrations
from mental_loader.accounts import recovery_code
from mental_loader.security import Vault, rate_limit, validate_hosted
from mental_loader.storage import (
    Conversation,
    IntegrationConnection,
    LoginSession,
    Message,
    OAuthAttempt,
    PushSubscription,
    RateBucket,
    User,
    digest,
    now,
)


@pytest.fixture
def secured(context, tmp_path):
    app, client, households, settings = context
    path = tmp_path / "key"
    path.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    path.chmod(0o600)
    settings.secret_key_path = path
    return context


def test_vault_rejects_ciphertext_swapping_and_unsafe_key_permissions(secured):
    _, _, _, settings = secured
    vault = Vault(settings.secret_key_path)
    sealed = vault.seal({"refresh_token": "private-fixture"}, "account:a")
    assert "private-fixture" not in sealed
    assert vault.open(sealed, "account:a") == {"refresh_token": "private-fixture"}
    with pytest.raises(InvalidTag):
        vault.open(sealed, "account:b")
    settings.secret_key_path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        Vault(settings.secret_key_path)


def test_hosted_mode_requires_personal_login_tls_and_secret_key(secured):
    _, _, _, settings = secured
    settings.hosted_mode = True
    with pytest.raises(ValueError, match="Hosted mode"):
        validate_hosted(settings)
    settings.public_origin = "https://household.example"
    settings.cookie_secure = True
    settings.allow_legacy_login = False
    settings.database_url = "postgresql+psycopg://unused/validation-only"
    validate_hosted(settings)
    settings.secret_key_path.unlink()
    with pytest.raises(OSError):
        validate_hosted(settings)


def test_recovery_is_one_time_and_revokes_all_sessions_and_devices(secured):
    app, client, households, _ = secured
    user_id, code = account(app, households[0][0])
    with app.state.sessions.begin() as db:
        recovery = recovery_code(db, db.get(User, user_id))
        db.add(
            PushSubscription(
                household_id=households[0][0],
                user_id=user_id,
                endpoint_hash=digest("fixture"),
                subscription={},
            )
        )
    sign_in(client, code)
    old_cookie = client.cookies.get("ml_session")
    response = client.post("/api/account/recover", json={"recovery_code": recovery})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    new = response.json()
    assert new["access_code"] != code and new["recovery_code"] != recovery
    assert client.post("/api/account/recover", json={"recovery_code": recovery}).status_code == 401
    assert client.post("/api/login", json={"access_code": code}).status_code == 401
    with app.state.sessions() as db:
        assert db.get(LoginSession, digest(old_cookie)) is None
        assert list(db.scalars(select(PushSubscription))) == []
    sign_in(client, new["access_code"])
    assert client.get("/api/me").json()["user_id"] == user_id
    # Wrong reauthentication cannot replace an account's credentials.
    assert client.post("/api/account/rotate", json={"access_code": "x" * 43}).status_code == 403
    assert (
        client.post("/api/account/rotate", json={"access_code": new["access_code"]}).status_code
        == 200
    )
    assert client.get("/api/me").status_code == 401


def test_atomic_rate_limits_do_not_leak_between_accounts(secured):
    app, _, _, _ = secured
    from concurrent.futures import ThreadPoolExecutor

    def claim(_):
        return rate_limit(app.state.sessions, "synthetic:account-a", 3)

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(claim, range(12))) == 3
    assert rate_limit(app.state.sessions, "synthetic:account-b", 3)
    assert rate_limit(
        app.state.sessions, "synthetic:account-a", 3, clock=now() + timedelta(minutes=2)
    )
    with app.state.sessions() as db:
        assert all("account" not in row.key for row in db.scalars(select(RateBucket)))


def test_login_attempts_are_limited_and_origins_protected(secured):
    _, client, _, _ = secured
    for _ in range(20):
        assert client.post("/api/login", json={"access_code": "x" * 43}).status_code == 401
    result = client.post("/api/login", json={"access_code": "x" * 43})
    assert result.status_code == 429 and result.headers["retry-after"]
    assert (
        client.post(
            "/api/account/recover",
            json={"recovery_code": "z" * 43},
            headers={"origin": "https://foreign.example"},
        ).status_code
        == 403
    )


def fake_google(monkeypatch):
    calls = []

    class Flow:
        def __init__(self, state, verifier):
            self.state = state
            self.code_verifier = verifier or "synthetic-pkce-" + state
            self.credentials = SimpleNamespace(
                granted_scopes=integrations.SCOPES,
                scopes=integrations.SCOPES,
                to_json=lambda: json.dumps({"refresh_token": "synthetic-google-secret"}),
            )

        def authorization_url(self, **kwargs):
            return f"https://accounts.google.com/authorize?state={self.state}", self.state

        def fetch_token(self, code):
            assert code == "synthetic-code"
            assert self.code_verifier == "synthetic-pkce-" + self.state
            calls.append(code)

    monkeypatch.setattr(
        integrations, "google_flow", lambda settings, state, verifier=None: Flow(state, verifier)
    )
    return calls


def test_google_oauth_is_session_bound_single_use_and_account_encrypted(secured, monkeypatch):
    app, client, households, settings = secured
    a_id, a_code = account(app, households[0][0])
    b_id, b_code = account(app, households[0][0], "Member", "member")
    calls = fake_google(monkeypatch)
    sign_in(client, a_code)
    assert client.post("/api/integrations/google/connect").status_code == 200
    with app.state.sessions() as db:
        attempt = db.scalar(select(OAuthAttempt))
        assert attempt.status == "pending"
    # Inspect only a synthetic URL from a second authorization request to obtain the state.
    url = client.post("/api/integrations/google/connect").json()["authorization_url"]
    state = url.split("state=")[1]
    sign_in(client, b_code)
    callback = f"/api/integrations/google/callback?state={state}&code=synthetic-code"
    assert client.get(callback).status_code == 400
    assert calls == []
    sign_in(client, a_code)
    # A different session of the same account also cannot finish the original attempt.
    assert client.get(callback).status_code == 400
    url = client.post("/api/integrations/google/connect").json()["authorization_url"]
    state = url.split("state=")[1]
    callback = f"/api/integrations/google/callback?state={state}&code=synthetic-code"
    assert client.get(callback, follow_redirects=False).status_code == 303
    assert calls == ["synthetic-code"]
    assert client.get(callback).status_code == 400
    assert client.get("/api/integrations").json() == {"connected": ["google-calendar"]}
    with app.state.sessions() as db:
        row = db.get(IntegrationConnection, (households[0][0], a_id, "google-calendar"))
        assert "synthetic-google-secret" not in row.encrypted_credentials
        assert (
            Vault(settings.secret_key_path).open(
                row.encrypted_credentials, integrations.scope(households[0][0], a_id)
            )["refresh_token"]
            == "synthetic-google-secret"
        )
    sign_in(client, b_code)
    assert client.get("/api/integrations").json() == {"connected": []}
    assert client.delete("/api/integrations/google").status_code == 200
    with app.state.sessions() as db:
        assert (
            db.get(IntegrationConnection, (households[0][0], a_id, "google-calendar")) is not None
        )


def test_google_disconnect_cancels_an_in_flight_authorization(secured, monkeypatch):
    app, client, households, _ = secured
    _, code = account(app, households[0][0])
    fake_google(monkeypatch)
    real_factory = integrations.google_flow

    def factory(*args, **kwargs):
        flow = real_factory(*args, **kwargs)
        original = flow.fetch_token

        def fetch_token(code):
            original(code)
            assert client.delete("/api/integrations/google").status_code == 200

        flow.fetch_token = fetch_token
        return flow

    monkeypatch.setattr(integrations, "google_flow", factory)
    sign_in(client, code)
    state = (
        client.post("/api/integrations/google/connect")
        .json()["authorization_url"]
        .split("state=")[1]
    )
    assert (
        client.get(
            f"/api/integrations/google/callback?state={state}&code=synthetic-code"
        ).status_code
        == 400
    )
    assert client.get("/api/integrations").json() == {"connected": []}


def test_real_google_library_generates_pkce_and_fixed_readonly_scope(secured, tmp_path):
    app, client, households, settings = secured
    _, code = account(app, households[0][0])
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "synthetic.apps.googleusercontent.com",
                    "client_secret": "synthetic-client",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        )
    )
    settings.google_web_client_path = path
    sign_in(client, code)
    result = client.post("/api/integrations/google/connect")
    assert result.status_code == 200
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(result.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == integrations.SCOPES
    assert query["redirect_uri"] == ["http://localhost:8000/api/integrations/google/callback"]
    with app.state.sessions() as db:
        assert query["state"][0] not in db.scalar(select(OAuthAttempt)).encrypted_verifier


def test_database_rejects_forged_ancestry_and_parent_reassignment(secured):
    app, client, households, _ = secured
    _, code = account(app, households[0][0])
    sign_in(client, code)
    chat = client.post("/api/conversations").json()["id"]
    with pytest.raises(IntegrityError), app.state.sessions.begin() as db:
        db.add(
            Message(
                household_id=households[1][0],
                conversation_id=chat,
                role="assistant",
                content="Forged",
            )
        )
    with pytest.raises(IntegrityError), app.state.sessions.begin() as db:
        db.execute(
            update(Conversation)
            .where(Conversation.id == chat)
            .values(household_id=households[1][0])
        )


def test_calendar_reader_never_uses_another_accounts_credentials(secured, monkeypatch):
    app, client, households, settings = secured
    a_id, a_code = account(app, households[0][0])
    b_id, b_code = account(app, households[0][0], "Member", "member")
    for user_id, token in ((a_id, "fixture-a"), (b_id, "fixture-b")):
        integrations.save_connection(
            app.state.sessions,
            settings,
            households[0][0],
            user_id,
            {
                "token": token,
                "refresh_token": "synthetic-refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "synthetic-client",
                "client_secret": "synthetic-secret",
                "expiry": "2099-01-01T00:00:00Z",
                "scopes": integrations.SCOPES,
            },
        )
    calls = []

    class Service:
        def __init__(self, token):
            self.token = token

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def events(self):
            return self

        def list(self, **kwargs):
            assert kwargs["calendarId"] == "primary"
            calls.append(self.token)
            return self

        def execute(self):
            return {"items": [{"summary": self.token}]}

    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *args, http, **kwargs: Service(http.credentials.token),
    )
    sign_in(client, a_code)
    result = client.get(f"/api/integrations/google/events?user_id={b_id}")
    assert result.json() == {"events": [{"summary": "fixture-a"}]}
    sign_in(client, b_code)
    assert client.get("/api/integrations/google/events").json() == {
        "events": [{"summary": "fixture-b"}],
    }
    assert calls == ["fixture-a", "fixture-b"]
    client.cookies.clear()
    assert (
        client.get(
            "/api/integrations/google/events",
            headers={
                "authorization": f"Bearer {households[0][1]}",
            },
        ).status_code
        == 401
    )


def test_existing_account_can_join_household_without_automatic_private_access(secured):
    from mental_loader.accounts import grant_membership

    app, client, households, _ = secured
    owner_id, owner_code = account(app, households[0][0])
    account(app, households[1][0])
    sign_in(client, owner_code)
    private = client.post("/api/conversations", json={"visibility": "private"}).json()["id"]
    with app.state.sessions.begin() as db:
        grant_membership(db, households[1][0], owner_id, "member")
    assert len(client.post("/api/login/options", json={"access_code": owner_code}).json()) == 2
    assert (
        client.post(
            "/api/login",
            json={
                "access_code": owner_code,
                "household_id": households[1][0],
            },
        ).status_code
        == 200
    )
    assert client.get(f"/api/conversations/{private}/messages").status_code == 404
    with pytest.raises(ValueError, match="last owner"), app.state.sessions.begin() as db:
        grant_membership(db, households[0][0], owner_id, "member")


def test_actual_request_body_limit_covers_chunked_and_invalid_length(secured):
    _, client, _, _ = secured
    response = client.post(
        "/api/login",
        content=iter([b"{" + b"x" * 5000, b"x" * 5000 + b"}"]),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert (
        client.post("/api/login", content=b"{}", headers={"content-length": "invalid"}).status_code
        == 400
    )


def test_account_switch_and_session_expiry_stop_old_device_notifications(secured):
    from mental_loader.accounts import subscription_authorized

    app, client, households, settings = secured
    settings.vapid_public_key = "fixture"
    a_id, a_code = account(app, households[0][0])
    _, b_code = account(app, households[0][0], "Member", "member")
    sign_in(client, a_code)
    body = subscription("device-bound")
    assert client.post("/api/push-subscriptions", json=body).status_code == 200
    with app.state.sessions() as db:
        row = db.scalar(select(PushSubscription))
        assert subscription_authorized(db, row)
        session_hash = row.session_hash
    with app.state.sessions.begin() as db:
        db.get(LoginSession, session_hash).expires_at = now() - timedelta(seconds=1)
    with app.state.sessions() as db:
        assert not subscription_authorized(db, db.scalar(select(PushSubscription)))
    # Even without JavaScript unsubscribe and without the previous login cookie,
    # the persistent HttpOnly device cookie retires this browser's old registration.
    client.cookies.delete("ml_session")
    sign_in(client, b_code)
    with app.state.sessions() as db:
        assert list(db.scalars(select(PushSubscription))) == []
