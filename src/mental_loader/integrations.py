"""Read-only Google Calendar, authorized and encrypted per account/membership."""

import secrets
from datetime import UTC, timedelta

from sqlalchemy import delete, select, update

from mental_loader.security import Vault
from mental_loader.storage import IntegrationConnection, Membership, OAuthAttempt, digest, now

SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def scope(household_id, user_id):
    return f"integration:{household_id}:{user_id}:google-calendar"


def google_flow(settings, state, verifier=None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(
        str(settings.google_web_client_path),
        scopes=SCOPES,
        state=state,
        redirect_uri=settings.public_origin.rstrip("/") + "/api/integrations/google/callback",
        code_verifier=verifier,
        autogenerate_code_verifier=verifier is None,
    )


def begin_google(sessions, settings, tenant, session_hash):
    state = secrets.token_urlsafe(32)
    vault = Vault(settings.secret_key_path)
    flow = google_flow(settings, state)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    with sessions.begin() as db:
        if db.get(Membership, (tenant.id, tenant.user_id)) is None:
            raise ValueError("Membership no longer active")
        db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < now()))
        db.add(
            OAuthAttempt(
                state_hash=digest(state),
                household_id=tenant.id,
                user_id=tenant.user_id,
                session_hash=session_hash,
                expires_at=now() + timedelta(minutes=10),
                encrypted_verifier=vault.seal(flow.code_verifier, f"oauth:{digest(state)}"),
            )
        )
    return url


def finish_google(sessions, settings, tenant, session_hash, state, code):
    vault = Vault(settings.secret_key_path)
    with sessions.begin() as db:
        attempt = db.scalar(
            select(OAuthAttempt)
            .where(
                OAuthAttempt.state_hash == digest(state),
                OAuthAttempt.household_id == tenant.id,
                OAuthAttempt.user_id == tenant.user_id,
                OAuthAttempt.session_hash == session_hash,
            )
            .with_for_update()
        )
        if attempt is None or attempt.status != "pending" or utc(attempt.expires_at) <= now():
            raise ValueError("OAuth request expired, was used, or belongs to a different session")
        verifier = vault.open(attempt.encrypted_verifier, f"oauth:{digest(state)}")
        # CAS is also required for SQLite, where FOR UPDATE has no effect.
        claimed = db.execute(
            update(OAuthAttempt)
            .where(
                OAuthAttempt.state_hash == attempt.state_hash,
                OAuthAttempt.status == "pending",
            )
            .values(status="exchanging")
        )
        if claimed.rowcount != 1:
            raise ValueError("OAuth request was already used")
    flow = google_flow(settings, state, verifier)
    flow.fetch_token(code=code)
    import json

    credentials = json.loads(flow.credentials.to_json())
    granted = set(flow.credentials.granted_scopes or flow.credentials.scopes or [])
    if not set(SCOPES).issubset(granted):
        raise ValueError("Calendar read permission was not granted")
    save_connection(
        sessions, settings, tenant.id, tenant.user_id, credentials, attempt_hash=digest(state)
    )


def save_connection(sessions, settings, household_id, user_id, credentials, attempt_hash=None):
    encrypted = Vault(settings.secret_key_path).seal(credentials, scope(household_id, user_id))
    with sessions.begin() as db:
        if db.get(Membership, (household_id, user_id)) is None:
            raise ValueError("Membership no longer active")
        if attempt_hash:
            attempt = db.scalar(
                select(OAuthAttempt)
                .where(
                    OAuthAttempt.state_hash == attempt_hash,
                    OAuthAttempt.household_id == household_id,
                    OAuthAttempt.user_id == user_id,
                    OAuthAttempt.status == "exchanging",
                    OAuthAttempt.expires_at > now(),
                )
                .with_for_update()
            )
            if attempt is None:
                raise ValueError("Authorization was cancelled or expired during exchange")
            db.delete(attempt)
        key = (household_id, user_id, "google-calendar")
        row = db.get(IntegrationConnection, key)
        if row is None:
            row = IntegrationConnection(
                household_id=household_id,
                user_id=user_id,
                provider="google-calendar",
                encrypted_credentials=encrypted,
            )
            db.add(row)
        else:
            row.encrypted_credentials = encrypted


def calendar_events(sessions, settings, household_id, user_id, *, birthdays=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    from httplib2 import Http

    vault = Vault(settings.secret_key_path)
    with sessions.begin() as db:
        if db.get(Membership, (household_id, user_id)) is None:
            raise ValueError("Membership no longer active")
        row = db.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.household_id == household_id,
                IntegrationConnection.user_id == user_id,
                IntegrationConnection.provider == "google-calendar",
            )
            .with_for_update()
        )
        if row is None:
            raise LookupError("Connect your Google Calendar first")
        credentials = Credentials.from_authorized_user_info(
            vault.open(row.encrypted_credentials, scope(household_id, user_id)),
            SCOPES,
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            import json

            row.encrypted_credentials = vault.seal(
                json.loads(credentials.to_json()), scope(household_id, user_id)
            )
        client = AuthorizedHttp(credentials, http=Http(timeout=10))
        with build("calendar", "v3", http=client, cache_discovery=False) as service:
            params = {
                "calendarId": "primary",
                "timeMin": now().isoformat(),
                "maxResults": 50,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if not birthdays:
                return service.events().list(**params).execute().get("items", [])
            params.update(
                timeMin=(now() - timedelta(days=2)).isoformat(),
                timeMax=(now() + timedelta(days=366)).isoformat(),
                eventTypes="birthday",
                showDeleted=True,
                maxResults=250,
            )
            events = []
            for _ in range(4):
                result = service.events().list(**params).execute()
                events.extend(result.get("items", []))
                token = result.get("nextPageToken")
                if not token:
                    return events
                params["pageToken"] = token
            raise ValueError("Birthday calendar exceeded the sync limit")
