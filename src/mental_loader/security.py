"""Scope-bound encryption, shared rate limits and hosted-mode prerequisites."""

import base64
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mental_loader.storage import RateBucket, digest, now


class Vault:
    def __init__(self, path):
        path = Path(path)
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Secret key must be a private regular file (mode 0600)")
        key = base64.urlsafe_b64decode(path.read_bytes().strip())
        if len(key) != 32:
            raise ValueError("Secret key must contain 32 random bytes, base64 encoded")
        self.cipher = AESGCM(key)

    def seal(self, value, scope):
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, json.dumps(value).encode(), scope.encode())
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def open(self, value, scope):
        raw = base64.urlsafe_b64decode(value)
        return json.loads(self.cipher.decrypt(raw[:12], raw[12:], scope.encode()))


def rate_limit(sessions, key, capacity, seconds=60, clock=None):
    """Atomic fixed-window quota, shared by workers; never store IPs or credentials."""
    current = int((clock or now()).timestamp())
    window = current - current % seconds
    # Include window length so differently configured quotas cannot alias.
    identity = digest(f"{seconds}:{key}")
    with sessions.begin() as db:
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        statement = insert(RateBucket).values(key=identity, bucket_start=window, count=1)
        statement = statement.on_conflict_do_update(
            index_elements=[RateBucket.key, RateBucket.bucket_start],
            set_={"count": RateBucket.count + 1},
            where=RateBucket.count < capacity,
        ).returning(RateBucket.count)
        allowed = db.scalar(statement) is not None
        db.execute(delete(RateBucket).where(RateBucket.bucket_start < current - 172800))
        return allowed


def validate_hosted(settings):
    if not settings.hosted_mode:
        return
    origin = urlparse(settings.public_origin)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or not settings.cookie_secure
        or settings.allow_legacy_login
        or not settings.database_url.startswith("postgresql")
    ):
        raise ValueError(
            "Hosted mode requires HTTPS, secure cookies, PostgreSQL and personal login"
        )
    Vault(settings.secret_key_path)


class BodyLimit:
    """Bound the actual ASGI request stream, including chunked or misleading bodies."""

    def __init__(self, app, max_bytes=8192):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)
