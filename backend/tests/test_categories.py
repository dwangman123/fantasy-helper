import pytest

from fantasy_helper.categories import categories_from_scoring_items

# Exactly what this league's settings return (captured 2026-10-20).
LEAGUE_ITEMS = [
    {"statId": 20, "isReverseItem": False},
    {"statId": 6, "isReverseItem": False},
    {"statId": 0, "isReverseItem": False},
    {"statId": 1, "isReverseItem": False},
    {"statId": 17, "isReverseItem": False},
    {"statId": 2, "isReverseItem": False},
    {"statId": 11, "isReverseItem": True},
    {"statId": 3, "isReverseItem": False},
    {"statId": 19, "isReverseItem": False},
]


def test_league_is_standard_nine_cat():
    cats = categories_from_scoring_items(LEAGUE_ITEMS)
    assert [c.key for c in cats] == ["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"]
    assert [c.key for c in cats if c.reverse] == ["TO"]
    fg = cats[0]
    assert fg.is_percentage and (fg.makes, fg.attempts) == ("FGM", "FGA")


def test_unknown_stat_id_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        categories_from_scoring_items([{"statId": 999}])


def test_reverse_flag_mismatch_is_rejected():
    with pytest.raises(ValueError, match="Reverse"):
        categories_from_scoring_items([{"statId": 11, "isReverseItem": False}])
