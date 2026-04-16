from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AdSync Pro API"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/adsync_pro"
    google_client_secrets_file: str = "gcp_client_secret.json"
    google_redirect_uri: str = "http://localhost:8000/auth/youtube/callback"
    google_token_uri: str = "https://oauth2.googleapis.com/token"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    secret_key: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    youtube_scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ]
    )
    fernet_key: str = Field(
        default="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        description="Base64-encoded key from Fernet.generate_key().decode()",
    )
    retention_lookback_days: int = 30

    @property
    def sync_database_url(self) -> str:
        if self.database_url.startswith("postgresql+asyncpg://"):
            return self.database_url.replace("+asyncpg", "+psycopg2", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()

