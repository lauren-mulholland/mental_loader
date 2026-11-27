import base64
import importlib.util
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mental_loader.config import Settings


def deployment_module(name):
    path = Path(__file__).parents[1] / "deploy" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"deploy_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+psycopg://"])
def test_paas_database_uses_installed_driver(prefix):
    url = prefix + "user:password@host/database?sslmode=require"
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url == (
        "postgresql+psycopg://user:password@host/database?sslmode=require"
    )


def test_readiness_checks_database_without_model(context):
    _, client, _, _ = context
    assert client.get("/healthz").json() == {"ok": True}


@pytest.mark.parametrize("name,link", [("../escape", False), ("/absolute", False),
                                      ("config.yaml", True)])
def test_restore_rejects_unsafe_archives(tmp_path, monkeypatch, name, link):
    monkeypatch.setenv("DEPLOYMENT_READY", "false")
    archive_path = tmp_path / "snapshot.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        else:
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="relative regular"):
        deployment_module("transfer").restore_home(archive_path, tmp_path / "home")
    assert not (tmp_path / "escape").exists()


def test_restore_preserves_existing_home_and_refuses_live_service(tmp_path, monkeypatch):
    module = deployment_module("transfer")
    home = tmp_path / "home"
    home.mkdir()
    saved = home / "MEMORY.md"
    saved.write_text("Preserve memory")
    monkeypatch.setenv("DEPLOYMENT_READY", "true")
    with pytest.raises(RuntimeError, match="DEPLOYMENT_READY=false"):
        module.restore_home(tmp_path / "absent.tar", home)
    monkeypatch.setenv("DEPLOYMENT_READY", "false")
    with pytest.raises(RuntimeError, match="empty Hermes home"):
        module.restore_home(tmp_path / "absent.tar", home)
    assert saved.read_text() == "Preserve memory"


def test_restored_home_only_marked_ready_after_valid_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_READY", "false")
    archive_path = tmp_path / "snapshot.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("config.yaml")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"{}\n"))
    home = tmp_path / "home"
    deployment_module("transfer").restore_home(archive_path, home)
    assert (home / "config.yaml").read_text() == "{}\n"
    assert (home / ".state-imported").is_file()


def test_hermes_startup_replaces_laptop_transport_but_preserves_memory(tmp_path, monkeypatch):
    module = deployment_module("start")
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: unchanged\n")
    (home / ".state-imported").touch()
    (home / "MEMORY.md").write_text("Stable preferences")
    (home / ".env").write_text("API_SERVER_HOST=127.0.0.1\nAPI_SERVER_KEY=old\n")
    for name, value in {
        "ML_BACKEND_URL": "https://household.onrender.com",
        "ML_HOUSEHOLD_ACCESS_CODE": "h" * 43,
        "API_SERVER_KEY": "k" * 43,
        "OPENROUTER_API_KEY": "fixture-provider-key",
    }.items():
        monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    module.prepare_hermes(home, 8642)
    env = (home / ".env").read_text()
    assert 'API_SERVER_HOST="0.0.0.0"' in env
    assert 'ML_BACKEND_URL="https://household.onrender.com"' in env
    assert 'API_SERVER_KEY="' + "k" * 43 + '"' in env
    assert (home / "MEMORY.md").read_text() == "Stable preferences"
    assert (home / "config.yaml").read_text() == "model: unchanged\n"
    assert len(calls) == 1


def test_hosted_startup_refuses_to_replace_persistent_encryption_key(tmp_path, monkeypatch):
    module = deployment_module("start")
    key = tmp_path / "app-secret.key"
    original = base64.urlsafe_b64encode(b"a" * 32)
    key.write_bytes(original)
    key.chmod(0o600)
    for name, value in {
        "RENDER_EXTERNAL_URL": "https://fixture.onrender.com",
        "HERMES_HOSTPORT": "fixture:8642",
        "DATABASE_URL": "postgresql://fixture/db",
        "HERMES_API_KEY": "k" * 43,
        "HERMES_HOUSEHOLD_ID": "fixture-household",
        "HERMES_TOOL_TOKEN": "t" * 43,
        "APP_SECRET_KEY_B64": base64.urlsafe_b64encode(b"b" * 32).decode(),
        "SECRET_KEY_PATH": str(key),
        "PUBLIC_ORIGIN": "https://fixture.onrender.com",
        "HERMES_BASE_URL": "http://fixture:8642",
        # Track variables the entrypoint enforces so they cannot leak into later tests.
        "COOKIE_SECURE": "false",
        "HOSTED_MODE": "false",
        "ALLOW_LEGACY_LOGIN": "true",
        "PAAS_PERSONAL_RUNTIME": "false",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="encryption key differs"):
        module.web_environment()
    assert key.read_bytes() == original


@pytest.mark.parametrize("variables,expected", [
    ({"RAILWAY_PUBLIC_DOMAIN": "fixture.up.railway.app"}, "https://fixture.up.railway.app"),
    ({"RENDER_EXTERNAL_URL": "https://fixture.onrender.com"}, "https://fixture.onrender.com"),
    ({"PUBLIC_ORIGIN": "https://household.example/", "RAILWAY_PUBLIC_DOMAIN": "ignored"},
     "https://household.example"),
])
def test_hosted_origin_uses_platform_or_explicit_domain(monkeypatch, variables, expected):
    for name in ("PUBLIC_ORIGIN", "RAILWAY_PUBLIC_DOMAIN", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert deployment_module("start").public_origin() == expected


@pytest.mark.parametrize("origin", [
    "http://household.example", "https://user:password@household.example",
    "https://household.example/path", "https://household.example?key=value",
    "https://configure-after-first-deploy.invalid",
])
def test_hosted_origin_rejects_setup_placeholders_and_non_origins(monkeypatch, origin):
    monkeypatch.setenv("PUBLIC_ORIGIN", origin)
    with pytest.raises(RuntimeError, match="hosted HTTPS origin"):
        deployment_module("start").public_origin()


def test_railway_input_export_keeps_app_identity_and_separates_agent_credentials(
    tmp_path, monkeypatch, capsys,
):
    import json

    from dotenv import dotenv_values

    module = deployment_module("transfer")
    module.ROOT = tmp_path
    local = tmp_path / ".local"
    home = local / "hermes-home"
    home.mkdir(parents=True)
    (local / "access-codes.json").write_text(json.dumps({"My household": {
        "household_id": "household-fixture", "access_code": "legacy-code-must-not-be-used",
    }}))
    (home / ".env").write_text("OPENROUTER_API_KEY=private-fixture-provider-key\n")
    private = local / "vapid.pem"
    private.write_bytes(b"fixture-key")
    settings = SimpleNamespace(
        vapid_private_key_path=private, vapid_public_key="fixture-public-key",
        vapid_subject="mailto:fixture@example.com", secret_key_path=local / "absent-app-key",
    )
    monkeypatch.setattr("mental_loader.config.Settings", lambda: settings)
    first, second = local / "first", local / "second"
    module.export_env(first)
    module.export_env(second)
    values, repeated = (dotenv_values(path / "shared.env") for path in (first, second))
    assert values["APP_SECRET_KEY_B64"] == repeated["APP_SECRET_KEY_B64"]
    assert values["HERMES_HOUSEHOLD_ID"] == "household-fixture"
    assert values["WEB_READY"] == values["HERMES_READY"] == "false"
    assert len(values["HERMES_TOOL_TOKEN"]) >= 32
    assert values["HERMES_TOOL_TOKEN"] != values["HERMES_API_KEY"]
    assert "legacy-code-must-not-be-used" not in (first / "shared.env").read_text()
    assert (first / "shared.env").stat().st_mode & 0o077 == 0
    assert "private-fixture-provider-key" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        module.export_env(first)
