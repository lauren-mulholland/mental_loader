"""Render entrypoints. Setup mode permits state transfer without workers or a scheduler."""

import base64
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def setup_server(port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            health = self.path == "/healthz"
            self.send_response(200 if health else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"setup": True, "ready": False}).encode())

        def log_message(self, *_args):
            pass

    print("Setup mode: restore personal state before enabling DEPLOYMENT_READY.", flush=True)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def require(name, minimum=1):
    value = os.environ.get(name, "").strip()
    if len(value) < minimum:
        raise RuntimeError(f"Configure {name} before enabling this service")
    return value


def web_environment():
    origin = os.environ.get("PUBLIC_ORIGIN") or require("RENDER_EXTERNAL_URL")
    if urlparse(origin).scheme != "https":
        raise RuntimeError("Hosted PUBLIC_ORIGIN must use HTTPS")
    os.environ["PUBLIC_ORIGIN"] = origin.rstrip("/")
    os.environ["COOKIE_SECURE"] = "true"
    os.environ["HOSTED_MODE"] = "true"
    os.environ["ALLOW_LEGACY_LOGIN"] = "false"
    os.environ["PAAS_PERSONAL_RUNTIME"] = "true"
    if not os.environ.get("HERMES_BASE_URL"):
        os.environ["HERMES_BASE_URL"] = "http://" + require("HERMES_HOSTPORT")
    require("DATABASE_URL")
    require("HERMES_API_KEY", 16)
    require("HERMES_HOUSEHOLD_ID")
    require("HERMES_TOOL_TOKEN", 32)
    app_key = base64.urlsafe_b64decode(require("APP_SECRET_KEY_B64"))
    if len(app_key) != 32:
        raise RuntimeError("APP_SECRET_KEY_B64 must encode 32 bytes")
    secret_path = Path(os.environ.get("SECRET_KEY_PATH", "/data/app-secret.key"))
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        if base64.urlsafe_b64decode(secret_path.read_bytes().strip()) != app_key:
            raise RuntimeError(
                "App encryption key differs from persistent state; preserve the old key"
            )
    else:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(base64.urlsafe_b64encode(app_key))
    os.environ["SECRET_KEY_PATH"] = str(secret_path)
    key = base64.b64decode(require("VAPID_PRIVATE_KEY_B64"), validate=True)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = serialization.load_pem_private_key(key, password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or private.curve.name != "secp256r1":
        raise RuntimeError("VAPID key must be a P-256 private key")
    public = base64.urlsafe_b64encode(private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )).rstrip(b"=").decode()
    if public != require("VAPID_PUBLIC_KEY"):
        raise RuntimeError("VAPID public and private keys do not match")
    require("VAPID_SUBJECT")
    path = Path("/tmp/mental-loader-vapid.pem")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key)
    os.environ["VAPID_PRIVATE_KEY_PATH"] = str(path)


def prepare_hermes(home, port):
    if not (home / "config.yaml").is_file() or not (home / ".state-imported").is_file():
        raise RuntimeError("Restore the private Hermes home before enabling this service")
    callback = require("ML_BACKEND_URL")
    parsed = urlparse(callback)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise RuntimeError("ML_BACKEND_URL must be an HTTPS backend origin")
    if parsed.hostname.endswith(".invalid"):
        raise RuntimeError("Set ML_BACKEND_URL to the hosted web URL")
    values = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_PORT": str(port),
        "API_SERVER_KEY": require("API_SERVER_KEY", 16),
        "ML_BACKEND_URL": callback.rstrip("/"),
        "ML_HOUSEHOLD_ACCESS_CODE": require("ML_HOUSEHOLD_ACCESS_CODE", 32),
        "OPENROUTER_API_KEY": require("OPENROUTER_API_KEY"),
    }
    # Hermes deliberately reloads its home .env with override=True. Write deployment
    # settings there too, so a restored laptop .env cannot change the bind or keys.
    descriptor = os.open(home / ".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name, value in values.items():
            stream.write(f"{name}={json.dumps(value)}\n")
    plugin = home / "plugins/mental-loader"
    plugin.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(ROOT / "hermes_plugin" / name, plugin / name)
    python = ROOT / ".local/hermes-runtime/.venv/bin/python"
    subprocess.run([str(python), str(ROOT / "scripts/configure_hermes.py")], check=True)


def main():
    role = sys.argv[1]
    if role not in {"web", "hermes"}:
        raise SystemExit("Choose web or hermes")
    port = int(os.environ.get("PORT", "10000" if role == "web" else "8642"))
    if os.environ.get("DEPLOYMENT_READY", "false").lower() != "true":
        setup_server(port)
        return
    os.chdir(ROOT)
    if role == "web":
        web_environment()
        # Ensure this is the restored personal household, not a fresh empty database.
        from sqlalchemy import select

        from mental_loader.config import Settings
        from mental_loader.storage import Household, Membership, database

        engine, sessions = database(Settings().database_url)
        with sessions() as db:
            if not db.scalar(select(Household.id).where(
                Household.id == os.environ["HERMES_HOUSEHOLD_ID"]
            )):
                raise RuntimeError("Restore the household database before enabling the app")
            if not db.scalar(select(Membership.user_id).where(
                Membership.household_id == os.environ["HERMES_HOUSEHOLD_ID"],
                Membership.role == "owner",
            ).limit(1)):
                raise RuntimeError("Create a personal household owner before enabling the app")
        engine.dispose()
        os.execvp("uvicorn", [
            "uvicorn", "mental_loader.app:create_app", "--factory", "--host", "0.0.0.0",
            "--port", str(port), "--workers", "1", "--timeout-graceful-shutdown", "20",
            "--no-access-log",
        ])
    else:
        prepare_hermes(Path(require("HERMES_HOME")), port)
        os.execvp("bash", ["bash", str(ROOT / "scripts/hermes.sh"), "gateway", "run"])


if __name__ == "__main__":
    main()
