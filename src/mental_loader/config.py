from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://mental_loader:local_only@127.0.0.1:55432/mental_loader",
        repr=False,
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def postgres_driver(cls, value):
        # PaaS URLs omit the driver; this project installs psycopg 3, not psycopg2.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    public_origin: str = "http://localhost:8000"
    cookie_secure: bool = False
    allow_legacy_login: bool = True
    hosted_mode: bool = False
    # Explicit single-household PaaS route; never supplies AI to private chats.
    paas_personal_runtime: bool = False
    hermes_tool_token: str = Field(default="", repr=False)
    secret_key_path: Path = Path(".local/app-secret.key")
    google_web_client_path: Path = Path(".local/google-web-client.json")
    ai_enabled: bool = False
    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = Field(default="", repr=False)
    hermes_household_id: str = ""
    ai_daily_call_limit: int = 20
    vapid_private_key_path: Path = Path(".local/vapid-private.pem")
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:replace-me@example.com"
    google_client_secret_path: Path = Path(".local/google-client.json")
