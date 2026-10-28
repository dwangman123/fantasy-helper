import numpy as np
import pytest
from sqlalchemy.orm import Session

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.waivers import (
    category_board,
    rank_hold,
    rank_stream,
    screen_stream,
    side_view,
    weekly_cat_p_win,
)
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.context import load_context
from fantasy_helper.db import queries as q
from fantasy_helper.domain import STAT_KEYS
from fantasy_helper.predict import predict_matchup
from fantasy_helper.waivers import _format, current_matchup, default_drops, waiver_report
from tests.league_fixture import MY_TEAM, SEASON, SLOTS, stored_league

PTS, STL, BLK, TO = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[2], KNOWN_CATEGORIES[1], KNOWN_CATEGORIES[11]
ANY = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT"]
DAYS = [1, 2, 3]


def player(pid, days=DAYS, priority=0.0, **rate):
    return SimPlayer(pid, ANY, {k: 0.0 for k in STAT_KEYS} | rate, {d: {} for d in days}, 1.0, priority)


def stream(cats, mine, opp, adds, drop_ids=(None,), first_day=1, days=DAYS, limit=25):
    return rank_stream(
        cats, {"home": {}, "away": {}}, {"home": mine, "away": opp}, "home", days, first_day, SLOTS,
        adds, list(drop_ids), limit=limit, n_sims=2000,
    )  # fmt: skip


def test_board_labels_categories_from_p_win():
    board = category_board({"PTS": 0.95, "STL": 0.5, "TO": 0.05, "BLK": 0.9, "AST": 0.1})
    assert board == {"PTS": "locked", "STL": "swing", "TO": "lost", "BLK": "swing", "AST": "swing"}


def test_help_in_a_swing_category_beats_help_in_a_locked_one():
    mine = [player(i, PTS=30, STL=1) for i in range(3)]
    opp = [player(10 + i, PTS=10, STL=1.2, BLK=3) for i in range(3)]
    scorer, thief = player(20, PTS=30), player(21, STL=2)
    ranking = stream([PTS, STL, BLK], mine, opp, [scorer, thief])
    assert ranking.board == {"PTS": "locked", "STL": "swing", "BLK": "lost"}
    best, other = ranking.options
    assert best.add_id == 21 and best.d_p_win > 0.2 and best.gained == ["STL"]
    assert other.add_id == 20 and abs(other.d_p_win) < 0.01
    assert [o.add_id for o in stream([PTS, STL, BLK], mine, opp, [scorer, thief], limit=1).options] == [21]


def test_hurting_a_lost_category_costs_nothing():
    mine = [player(i, PTS=30, STL=1, TO=6) for i in range(3)]
    opp = [player(10 + i, PTS=10, STL=1, TO=1) for i in range(3)]
    sloppy, clean = player(20, STL=2, TO=5), player(21, STL=2)
    ranking = stream([PTS, STL, TO], mine, opp, [sloppy, clean])
    by_id = {o.add_id: o for o in ranking.options}
    assert ranking.board["TO"] == "lost"
    assert by_id[20].d_p_win == pytest.approx(by_id[21].d_p_win, abs=0.005) and by_id[20].d_p_win > 0.2


def test_dropped_player_keeps_his_games_before_the_pickup_can_play():
    dropped, pickup = player(1, days=(1, 2), PTS=10), player(20, days=(2,), PTS=12)
    [option] = stream([PTS], [dropped], [player(10, PTS=10)], [pickup], drop_ids=[1], first_day=2).options
    assert option.drop_id == 1 and option.extra_starts == 0  # day 1 his, day 2 the pickup's


def test_free_agent_without_a_playable_game_is_not_evaluated():
    ranking = stream([PTS], [player(1, PTS=10)], [player(10, PTS=10)], [player(20, days=(1,), PTS=30)], first_day=2)
    assert ranking.options == [] and ranking.screened == 0


def test_screen_prefers_games_he_would_actually_start():
    full_day = [player(i, days=(1,), priority=10) for i in range(10)]
    benched, starter = player(20, days=(1,), PTS=1), player(21, days=(2,), PTS=1)
    slopes = np.array([1.0 if k == "PTS" else 0.0 for k in STAT_KEYS])
    assert screen_stream([benched, starter], full_day, SLOTS, slopes, limit=1) == [starter]


def test_screens_rank_more_games_above_the_same_player_with_fewer():
    # Below my roster's per-game average, so his z-scores are negative: the screens must
    # still count each extra game as extra help.
    weeks = [[1, 2, 3], [4, 5, 6]]
    days = [d for w in weeks for d in w]
    mine = [player(i, days=days, PTS=12, STL=1) for i in range(3)]
    opp = [player(10 + i, days=days, PTS=12, STL=1) for i in range(3)]
    busy, idle = player(20, days=days, PTS=6, STL=0.5), player(21, days=(1, 4), PTS=6, STL=0.5)
    week = stream([PTS, STL], mine, opp, [idle, busy], days=days, limit=1)
    assert week.screened == 1 and [o.add_id for o in week.options] == [20]
    season = rank_hold([PTS, STL], mine, [mine, opp], weeks, SLOTS, [idle, busy], [None], limit=1)
    assert season.screened == 1 and [o.add_id for o in season.options] == [20]


def test_hold_values_games_and_picks_the_weakest_drop():
    weeks = [[1, 2, 3], [4, 5, 6]]
    days = [d for w in weeks for d in w]
    mine = [player(i, days=days, priority=10 - i, PTS=15 - i, STL=1) for i in range(5)]
    league = [mine, [player(10 + i, days=days, PTS=12, STL=1) for i in range(5)]]
    every_day, half = player(20, days=days, PTS=14, STL=1.5), player(21, days=(1, 3, 5), PTS=14, STL=1.5)
    ranking = rank_hold([PTS, STL], mine, league, weeks, SLOTS, [every_day, half], [4, 3])
    every, half = ranking.options
    assert (every.add_id, every.drop_id, half.add_id) == (20, 4, 21)
    # Half the games of my weakest player is a step down, despite the better per-game line.
    assert every.d_cats_per_week > 0 > half.d_cats_per_week


def test_weekly_p_win_is_even_for_equal_teams_and_reverses_turnovers():
    col = {k: i for i, k in enumerate(STAT_KEYS)}
    me, opp = [[0.0] * len(STAT_KEYS)], [[0.0] * len(STAT_KEYS)]
    me[0][col["PTS"]] = opp[0][col["PTS"]] = 400
    me[0][col["TO"]], opp[0][col["TO"]] = 40, 60
    p = weekly_cat_p_win(np.array(me), np.array(opp), [PTS, TO], {})
    assert p["PTS"][0] == pytest.approx(0.5) and p["TO"][0] > 0.9


@pytest.fixture(scope="module")
def league():
    engine = stored_league()
    with Session(engine) as s:
        yield s, load_context(s, SEASON)


def test_report_baseline_is_this_matchups_prediction(league):
    session, ctx = league
    mu = current_matchup(session, ctx, MY_TEAM)
    report = waiver_report(session, SEASON, MY_TEAM, max_adds=3, n_sims=800, ctx=ctx)
    expected = side_view(predict_matchup(ctx, mu, n_sims=800), report.side)
    assert report.stream.baseline == expected  # same random numbers as the Matchup page
    assert report.first_day == ctx.scoring_period + 1
    assert 0 < len(report.stream.options) <= 3 and 0 < len(report.hold.options) <= 3
    assert report.hold_periods[0] == ctx.matchup_period + 1
    teams = {t.team_id: t for t in q.load_teams(session, SEASON)}
    assert "Stream (this week" in _format(report, ctx, teams, top=3)


def test_default_drops_are_my_lowest_priority_players(league):
    _, ctx = league
    drops = default_drops(ctx, MY_TEAM)
    mine = sorted((r.player_id for r in ctx.team_entries(MY_TEAM)), key=lambda pid: ctx.z[pid].total)
    assert drops == mine[:3]  # full roster, so no open-spot option


def test_drops_must_be_my_players(league):
    session, ctx = league
    other = next(r.player_id for r in ctx.rosters if r.team_id != MY_TEAM)
    with pytest.raises(ValueError, match="Drops must be"):
        waiver_report(session, SEASON, MY_TEAM, drop_ids=[other], ctx=ctx)
