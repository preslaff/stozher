from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parents[4] / "infra" / ".env"  # git-ignored; see infra/.env.example


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None, extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://preslaff@/stozher?host=/var/run/postgresql"
    # 32 random bytes as hex; encrypts node master keys at rest. Required to register nodes.
    stozher_kek: str = ""
    # Creates the first admin key on startup if no admin key exists.
    stozher_bootstrap_admin_key: str = ""
    # Ed25519 private seed (32 bytes as 64 hex) that signs acknowledgements; absent = acks disabled.
    stozher_ed25519_seed: str = ""
    cloud_key_id: int = 1
    # Community hazard reports (docs/hazards.md): limits per reporter key, counted in the database.
    report_limit_per_hour: int = 20
    report_limit_per_day: int = 120
    report_max_age_h: int = 48  # a report must describe something that happened recently
    # Accept records whose node clock is within this window of server time.
    replay_window_s: int = 2 * 86400
    env_name: str = "live"
    # Browser origins allowed to call the API (the dashboard); comma separated.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
