import pytest

from fantasy_helper import config
from fantasy_helper.config import Settings


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_postgres_urls_use_psycopg(scheme):
    url = Settings(database_url=f"{scheme}://u:p@db.example.com/fantasy?sslmode=require").database_url
    assert url == "postgresql+psycopg://u:p@db.example.com/fantasy?sslmode=require"


def test_sqlite_urls_are_left_alone():
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_deployed_values_come_from_the_aws_secret_and_env_wins(monkeypatch):
    secret = {"FH_DATABASE_URL": "postgresql://u:p@neon.example.com/db", "FH_MY_TEAM_ID": "7", "OTHER": "x"}
    monkeypatch.setattr(config, "read_aws_secret", lambda secret_id: secret if secret_id == "fh/app" else {})
    monkeypatch.setenv("FH_SECRET_ID", "fh/app")
    monkeypatch.setenv("FH_MY_TEAM_ID", "5")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@neon.example.com/db"
    assert settings.my_team_id == 5


def test_no_secret_is_read_without_a_secret_id(monkeypatch):
    def fail(secret_id):
        raise AssertionError("read the secret")

    monkeypatch.setattr(config, "read_aws_secret", fail)
    monkeypatch.delenv("FH_SECRET_ID", raising=False)
    assert Settings(_env_file=None).league_id == 1396633554
