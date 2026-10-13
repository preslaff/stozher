from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://preslaff@/stozher?host=/var/run/postgresql"
    # 32 random bytes as hex; encrypts node master keys at rest. Required to register nodes.
    stozher_kek: str = ""
    # Creates the first admin key on startup if no admin key exists.
    stozher_bootstrap_admin_key: str = ""
    # Accept records whose node clock is within this window of server time.
    replay_window_s: int = 2 * 86400
    env_name: str = "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
