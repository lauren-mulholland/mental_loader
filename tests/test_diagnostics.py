import json

import httpx

from mental_loader.config import Settings
from mental_loader.diagnostics import readiness


def test_readiness_never_exposes_credentials_or_calls_inference(tmp_path, monkeypatch):
    monkeypatch.setattr("mental_loader.diagnostics.ROOT", tmp_path)
    home = tmp_path / ".local/hermes-home"
    home.mkdir(parents=True)
    secret = "fixture-private-provider-key-should-not-appear"
    (home / ".env").write_text(f"OPENROUTER_API_KEY={secret}\n")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/test.db",
        hermes_api_key="fixture-private-gateway-key",
    )
    paths = []
    real_client = httpx.Client

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        "mental_loader.diagnostics.httpx.Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    checks = readiness(settings)
    serialised = json.dumps(checks)
    assert secret not in serialised
    assert settings.hermes_api_key not in serialised
    assert paths == ["/health", "/v1/capabilities"]
    assert any(row["check"] == "PWA chat enabled" and not row["ready"] for row in checks)
