from dataclasses import replace

import numpy as np
import pytest
from sqlalchemy.orm import Session

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.trades import (
    MIN_GAIN,
    evaluate_trade,
    find_trades,
    first_order,
    season,
    week_odds,
)
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.context import load_context
from fantasy_helper.domain import STAT_KEYS, Injury
from fantasy_helper.trades import season_outlook, trade_ideas, trade_report
from tests.league_fixture import MY_TEAM, SEASON, SLOTS, stored_league

PTS, STL = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[2]
ANY = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT"]
WEEKS = [list(range(1, 8)), list(range(8, 15))]
DAYS = [d for w in WEEKS for d in w]


def player(pid, priority=0.0, days=DAYS, **rate):
    return SimPlayer(pid, ANY, {k: 0.0 for k in STAT_KEYS} | rate, {d: {} for d in days}, 1.0, priority)


def league(free_agents=(), spots=3):
    """Team 1 is safely ahead in PTS and a little behind in STL; team 2 the other way round."""
    rosters = {
        1: [player(10 + i, priority=1 - i, PTS=20, STL=1.1) for i in range(3)],
        2: [player(20 + i, priority=1 - i, PTS=14, STL=2) for i in range(3)],
        3: [player(30 + i, PTS=15, STL=1.25) for i in range(3)],
        4: [player(40 + i, PTS=15, STL=1.25) for i in range(3)],
    }
    return season([PTS, STL], rosters, WEEKS, SLOTS, spots, list(free_agents))


def test_week_odds_count_categories_won():
    win, tie = week_odds({"A": np.array([0.5]), "B": np.array([0.5]), "C": np.array([0.5])})
    assert win[0] == pytest.approx(0.5) and tie[0] == 0
    win, _ = week_odds({"A": np.array([0.9]), "B": np.array([0.9]), "C": np.array([0.1])})
    assert win[0] == pytest.approx(0.81 + 2 * 0.9 * 0.1 * 0.1)  # both 90% ones, or one of them plus the 10%
    win, tie = week_odds({"A": np.array([0.5]), "B": np.array([0.5])})
    assert (win[0], tie[0]) == pytest.approx((0.25, 0.5))  # an even number of categories can tie


def test_a_scorer_for_a_thief_helps_both_teams():
    r = evaluate_trade(league(), 1, 2, [10], [20])
    assert r.me.d_cats_per_week > 0.1 and r.partner.d_cats_per_week > 0.1
    assert r.me.cat_d_p_win["STL"] > 0 > r.me.cat_d_p_win["PTS"]
    assert r.me.cat_d_value["PTS"] == pytest.approx(-42) and r.me.cat_d_value["STL"] == pytest.approx(6.3)
    assert r.me.d_p_week > 0 and (r.me.drops, r.me.adds) == ([], [])


def test_trade_reads_the_same_from_either_side():
    s = league()
    mine, theirs = evaluate_trade(s, 1, 2, [10], [20, 21]), evaluate_trade(s, 2, 1, [20, 21], [10])
    assert mine.me == theirs.partner and mine.partner == theirs.me


def test_two_for_one_keeps_both_rosters_legal():
    s = league(free_agents=[player(90, PTS=8, STL=1), player(91, PTS=2)])
    r = evaluate_trade(s, 1, 2, [10, 11], [22])
    assert r.me.adds == [90] and r.me.drops == []  # the best free agent fills the spot I opened
    assert r.partner.drops == [21] and r.partner.adds == []  # their lowest priority outside the deal


def test_first_order_estimate_tracks_a_small_swap():
    s = league()
    s.rosters[2].append(player(29, priority=-5, PTS=19, STL=1.3))
    s = season([PTS, STL], s.rosters, WEEKS, SLOTS, 4, [])
    exact = evaluate_trade(s, 1, 2, [10], [29]).me.d_cats_per_week
    assert exact > 0 and first_order(s, 1, (10,), (29,)) == pytest.approx(exact, rel=0.2)


def test_finder_lists_only_deals_good_for_both_and_skips_untradable_players():
    s = league()
    found = find_trades(s, 1, tradable={10, 11, 12, 20, 21, 30, 31, 32, 40, 41, 42})
    assert found.deals and found.evaluated <= found.candidates
    assert all(min(d.me.d_cats_per_week, d.partner.d_cats_per_week) >= MIN_GAIN for d in found.deals)
    assert {d.partner.team_id for d in found.deals} == {2}  # only the thieves fill my hole
    assert all(22 not in d.me.receives for d in found.deals)
    ranked = [d.me.d_cats_per_week for d in found.deals]
    assert ranked == sorted(ranked, reverse=True)
    assert len({frozenset(d.me.receives) for d in found.deals}) == len(found.deals)


@pytest.fixture(scope="module")
def stored():
    engine = stored_league()
    with Session(engine) as s:
        ctx = load_context(s, SEASON)
    return ctx, season_outlook(ctx)


def test_outlook_covers_the_weeks_after_this_one(stored):
    ctx, outlook = stored
    assert outlook.weeks[0][0] == ctx.calendar.week(ctx.matchup_period + 1).first_scoring_period
    assert len(outlook.rosters) == 10 and all(sum(p.ir_back is None for p in r) == 13 for r in outlook.rosters.values())
    # Each team's IR player has no return date in the fixture, so he's back after this week.
    assert all([p.ir_back for p in r if p.ir_back] == [outlook.weeks[0][0]] for r in outlook.rosters.values())
    assert all(fills for fills in outlook.fills.values())


def test_report_checks_the_deal_and_warns_about_injuries(stored):
    ctx, outlook = stored
    mine, theirs = (outlook.rosters[t][0].player_id for t in (MY_TEAM, 7))
    report = trade_report(ctx, outlook, MY_TEAM, 7, [mine], [theirs])
    assert report.result.me.team_id == MY_TEAM and report.warnings == [] and report.assumptions
    assert report.result.me.drops == []  # the IR player holds no roster spot, so 1-for-1 needs no drop
    ir = next(p.player_id for p in outlook.rosters[7] if p.ir_back is not None)
    with pytest.raises(ValueError, match="outside the IR slot"):
        trade_report(ctx, outlook, MY_TEAM, 7, [mine], [ir])
    with pytest.raises(ValueError, match="on its roster"):
        trade_report(ctx, outlook, MY_TEAM, 7, [theirs], [mine])
    with pytest.raises(ValueError, match="1 to 3"):
        trade_report(ctx, outlook, MY_TEAM, 7, [], [theirs])
    with pytest.raises(ValueError, match="two different teams"):
        trade_report(ctx, outlook, MY_TEAM, MY_TEAM, [mine], [mine])
    out = replace(ctx, players=ctx.players | {theirs: replace(ctx.players[theirs], injury_status="OUT")})
    [warning] = trade_report(out, outlook, MY_TEAM, 7, [mine], [theirs]).warnings
    assert "is out with no return date listed" in warning
    back = ctx.calendar.week(ctx.matchup_period + 2).start
    dated = replace(out, injuries={theirs: Injury(theirs, back, False, "Knee")})
    [warning] = trade_report(dated, season_outlook(dated), MY_TEAM, 7, [mine], [theirs]).warnings
    assert f"out until {back:%b} {back.day}" in warning
    gone = replace(out, injuries={theirs: Injury(theirs, None, True, "Knee")})
    [warning] = trade_report(gone, season_outlook(gone), MY_TEAM, 7, [mine], [theirs]).warnings
    assert "out for the season" in warning


def test_ideas_leave_out_players_whose_value_cant_be_told(stored):
    ctx, outlook = stored
    pid = outlook.rosters[3][0].player_id
    out = replace(ctx, players=ctx.players | {pid: replace(ctx.players[pid], injury_status="OUT")})
    ideas = trade_ideas(out, outlook, MY_TEAM, per_shape=3)
    assert ideas.search.evaluated <= 9 * 3 * 3 and ideas.assumptions
    assert all(pid not in d.me.receives for d in ideas.search.deals)
    assert all(MY_TEAM == d.me.team_id != d.partner.team_id for d in ideas.search.deals)
    # Out for the season: still left out. Back in two weeks: dealt for the games he has left.
    gone = replace(out, injuries={pid: Injury(pid, None, True, "Knee")})
    assert trade_ideas(gone, season_outlook(gone), MY_TEAM, per_shape=3).search.candidates == ideas.search.candidates
    back = replace(out, injuries={pid: Injury(pid, ctx.calendar.week(ctx.matchup_period + 2).start, False, "Knee")})
    assert trade_ideas(back, season_outlook(back), MY_TEAM, per_shape=3).search.candidates > ideas.search.candidates
