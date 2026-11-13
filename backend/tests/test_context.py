from dataclasses import replace

import numpy as np
import pytest
from sqlalchemy.orm import Session

from fantasy_helper.context import injury_assumption, load_context
from fantasy_helper.domain import Injury
from fantasy_helper.trades import season_outlook
from tests.league_fixture import MY_TEAM, SEASON, stored_league


@pytest.fixture(scope="module")
def ctx():
    with Session(stored_league()) as s:
        return load_context(s, SEASON)


def hurt(ctx, pid, status="OUT", injury=None):
    """`ctx` with `pid` listed as `status` and, if given, ESPN's injury entry for him."""
    players = ctx.players | {pid: replace(ctx.players[pid], injury_status=status)}
    injuries = ctx.injuries | ({pid: injury} if injury else {})
    return replace(ctx, players=players, injuries=injuries)


def later_days(ctx):
    return [d for w in ctx.later_weeks for d in w.scoring_periods]


def starter(ctx):
    return ctx.team_entries(MY_TEAM)[0].player_id


def test_rest_of_season_counts_games_from_the_expected_return(ctx):
    pid, days = starter(ctx), later_days(ctx)
    back = ctx.later_weeks[2].first_scoring_period
    c = hurt(ctx, pid, injury=Injury(pid, ctx.calendar.date_for(back), False, "Knee"))
    healthy = ctx.sim_player(pid, days, rest_of_season=True)
    injured = c.sim_player(pid, days, rest_of_season=True)
    assert c.return_period(pid) == back
    assert injured.games and min(injured.games) >= back and injured.p_play == 1.0
    assert set(injured.games) == {d for d in healthy.games if d >= back}
    # He's back after this week, so this week's sim has him out every day.
    this_week = c.sim_player(pid, ctx.days_left)
    assert all(this_week.p_play_on(d) == 0.0 for d in ctx.days_left)


def test_out_for_the_season_has_no_games(ctx):
    pid = starter(ctx)
    c = hurt(ctx, pid, injury=Injury(pid, None, True, "Knee"))
    assert c.sim_player(pid, later_days(ctx), rest_of_season=True).games == {}


def test_an_active_player_ignores_a_stale_injury_entry(ctx):
    pid, days = starter(ctx), later_days(ctx)
    c = hurt(ctx, pid, status="ACTIVE", injury=Injury(pid, None, True, "Knee"))
    assert c.return_period(pid) is None
    assert c.sim_player(pid, days, rest_of_season=True).games == ctx.sim_player(pid, days, rest_of_season=True).games


def test_out_with_no_return_date_counts_as_healthy_and_is_named(ctx):
    pid, days = starter(ctx), later_days(ctx)
    c = hurt(ctx, pid)
    assert c.unknown_returns([pid]) == [pid] and c.return_period(pid) is None
    assert c.sim_player(pid, days, rest_of_season=True).games == ctx.sim_player(pid, days, rest_of_season=True).games
    # The fixture has other OUT players; give them all a date so only he is undated.
    dated = replace(ctx, injuries={p: Injury(p, None, True, "Knee") for p in ctx.unknown_returns(ctx.players)})
    assert "no return date" not in injury_assumption(dated)
    assert f"No return date is listed for {ctx.players[pid].name}," in injury_assumption(hurt(dated, pid))
    assert f"{len(c.unknown_returns(c.players))} injured players have no return date" in injury_assumption(c)


def test_the_bench_covers_an_injured_starters_weeks(ctx):
    """My weekly totals fall only in the weeks he misses; the lineup cap starts someone else."""
    pid = max(ctx.team_entries(MY_TEAM), key=lambda r: ctx.z[r.player_id].value).player_id
    back = ctx.later_weeks[2].first_scoring_period
    c = hurt(ctx, pid, injury=Injury(pid, ctx.calendar.date_for(back), False, "Knee"))
    before, after = season_outlook(ctx).weekly[MY_TEAM], season_outlook(c).weekly[MY_TEAM]
    assert np.all(after[:2] <= before[:2]) and after[:2].sum() < before[:2].sum()
    np.testing.assert_allclose(after[2:], before[2:])


def test_a_player_in_the_ir_slot_joins_his_team_when_back(ctx):
    pid = next(r.player_id for r in ctx.rosters if r.team_id == MY_TEAM and r.lineup_slot == "IR")
    days = later_days(ctx)
    ids = lambda c: {p.player_id: p for p in c.team_sim_players(MY_TEAM, days, rest_of_season=True)}  # noqa: E731
    assert pid not in {p.player_id for p in ctx.team_sim_players(MY_TEAM, ctx.days_left)}  # this week: off
    assert ids(ctx)[pid].ir_back == days[0]  # no return date listed: back after this week
    back = ctx.later_weeks[1].first_scoring_period
    dated = ids(hurt(ctx, pid, injury=Injury(pid, ctx.calendar.date_for(back), False, "Knee")))[pid]
    assert dated.ir_back == back and min(dated.games) >= back
    assert pid not in ids(hurt(ctx, pid, injury=Injury(pid, None, True, "Knee")))
    assert all(p.ir_back is None for p in ids(ctx).values() if p.player_id != pid)


def test_this_week_an_injured_player_is_out_until_his_return_then_day_to_day(ctx):
    pid, days = starter(ctx), ctx.days_left
    back = days[1]
    c = hurt(ctx, pid, injury=Injury(pid, ctx.calendar.date_for(back), False, "Ankle"))
    sim = c.sim_player(pid, days)
    assert [sim.p_play_on(d) for d in days] == [0.0] + [0.5] * (len(days) - 1)
    assert c.sim_player(pid, days, rest_of_season=True).out_before is None  # rest of season: every game from then
    # Out for the season, or no date listed: out all week.
    for injury in (Injury(pid, None, True, "Knee"), None):
        sim = hurt(ctx, pid, injury=injury).sim_player(pid, days)
        assert all(sim.p_play_on(d) == 0.0 for d in days)
    # Day-to-day ignores the date: 50% every game.
    sim = hurt(
        ctx, pid, status="DAY_TO_DAY", injury=Injury(pid, ctx.calendar.date_for(days[-1]), False, "Ankle")
    ).sim_player(pid, days)
    assert sim.out_before is None and sim.p_play == 0.5
