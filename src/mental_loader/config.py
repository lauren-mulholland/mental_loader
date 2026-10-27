from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://mental_loader:local_only@127.0.0.1:55432/mental_loader"
    )
    public_origin: str = "http://localhost:8000"
    cookie_secure: bool = False
    ai_enabled: bool = False
    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_household_id: str = ""
    ai_daily_call_limit: int = 20
    vapid_private_key_path: Path = Path(".local/vapid-private.pem")
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:replace-me@example.com"
    google_client_secret_path: Path = Path(".local/google-client.json")
