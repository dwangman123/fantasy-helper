import pytest
from sqlalchemy import create_engine, func, select

from fantasy_helper.db import models as m
from fantasy_helper.db.copy import copy_database
from tests.league_fixture import stored_league


def row_counts(engine):
    with engine.connect() as conn:
        return {t.name: conn.scalar(select(func.count()).select_from(t)) for t in m.Base.metadata.sorted_tables}


def test_copy_moves_every_row_and_refuses_a_filled_target():
    source = stored_league()
    target = create_engine("sqlite://")
    m.Base.metadata.create_all(target)
    counts = copy_database(source, target)
    assert counts == row_counts(source) == row_counts(target) and counts["players"] > 0
    with pytest.raises(ValueError, match="not empty"):
        copy_database(source, target)
