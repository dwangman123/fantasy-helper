from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration, read from env vars prefixed FH_ (or backend/.env)."""

    model_config = SettingsConfigDict(env_prefix="FH_", env_file=BACKEND_DIR / ".env", extra="ignore")

    league_id: int = 1396633554
    season: int = 2027  # ESPN labels a season by the year it ends: 2027 = the 2026-27 season
    my_team_id: int = 4
    # Private leagues only; this league is public so both stay unset.
    espn_s2: str | None = None
    swid: str | None = None

    database_url: str = f"sqlite:///{BACKEND_DIR / 'fantasy.db'}"
    free_agent_pool_size: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
