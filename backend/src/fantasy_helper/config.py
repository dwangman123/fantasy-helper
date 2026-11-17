import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


def psycopg_url(url: str) -> str:
    """Hosted Postgres hands out postgres:// or postgresql:// URLs, which SQLAlchemy would open
    with psycopg2; psycopg (3) is the driver installed."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


class Settings(BaseSettings):
    """Runtime configuration, read from env vars prefixed FH_ (or backend/.env), and when deployed
    from the JSON secret named by FH_SECRET_ID (see AwsSecretSource)."""

    model_config = SettingsConfigDict(env_prefix="FH_", env_file=BACKEND_DIR / ".env", extra="ignore")

    league_id: int = 1396633554
    season: int = 2027  # ESPN labels a season by the year it ends: 2027 = the 2026-27 season
    my_team_id: int = 4
    # Private leagues only; this league is public so both stay unset.
    espn_s2: str | None = None
    swid: str | None = None

    database_url: str = f"sqlite:///{BACKEND_DIR / 'fantasy.db'}"
    free_agent_pool_size: int = 200
    # A refresh within this many seconds of the last successful ingest returns that run instead
    # of fetching again. Off locally; the deployed API sets it, since anyone can press Refresh.
    refresh_cooldown_seconds: int = 0

    # The chat agent (agent/). Off until the key, the model and its prices are all set; the
    # prices are USD per million tokens, from OpenAI's pricing page, and price each turn.
    openai_api_key: str | None = None
    agent_model: str | None = None
    agent_input_price: float | None = None
    agent_cached_input_price: float | None = None
    agent_output_price: float | None = None
    agent_reasoning_effort: str | None = None  # e.g. "low"; left out of requests when unset
    # Reasoning models count their reasoning against this too, so it's generous.
    agent_max_output_tokens: int = 8000
    agent_max_steps: int = 6  # model calls per turn; the last can't call tools
    # Below the API function's 90 s timeout (infra/terraform), with room to send the last events.
    agent_deadline_seconds: float = 75.0

    @property
    def agent_configured(self) -> bool:
        prices = (self.agent_input_price, self.agent_cached_input_price, self.agent_output_price)
        return bool(self.openai_api_key and self.agent_model) and None not in prices

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, url: str) -> str:
        return psycopg_url(url)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, dotenv_settings, AwsSecretSource(settings_cls), file_secret_settings


def read_aws_secret(secret_id: str) -> dict[str, str]:
    import boto3  # only in the deployed image (the `aws` extra)

    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])


class AwsSecretSource(PydanticBaseSettingsSource):
    """FH_* values from a JSON object in AWS Secrets Manager, e.g. {"FH_DATABASE_URL": "..."},
    when FH_SECRET_ID names it. Env vars take precedence. One secret holds every deployed
    secret, so adding one (the OpenAI key, say) needs no infrastructure change."""

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        secret_id = os.environ.get("FH_SECRET_ID")
        self.values = read_aws_secret(secret_id) if secret_id else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self.values.get(f"FH_{field_name.upper()}"), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            name: value
            for name, field in self.settings_cls.model_fields.items()
            if (value := self.get_field_value(field, name)[0]) is not None
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
