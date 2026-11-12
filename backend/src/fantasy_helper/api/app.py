"""REST API over the stored snapshot; `fh-api` serves it with uvicorn.

Everything comes from the latest ingest, which `POST /api/refresh` can also run. Simulations are cached in memory per
snapshot (keyed by the league row's updated_at), so the next request after an
ingest recomputes. Matchup and waiver views are from one team's side, mine
unless `team_id` says otherwise.
"""

import argparse
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fantasy_helper.analytics.actuals import category_results, category_values
from fantasy_helper.analytics.daily_lineup import SLOT_ORDER, team_week
from fantasy_helper.analytics.power import all_play_records, power_rankings
from fantasy_helper.analytics.team_profile import DEFAULT_PUNT_Z, TeamProfile, team_profiles
from fantasy_helper.analytics.trades import Season, TradeSide
from fantasy_helper.analytics.waivers import category_board, side_view
from fantasy_helper.analytics.zscores import SPECIALTY_Z, PlayerZ, player_zscores, specialties
from fantasy_helper.api import schemas as s
from fantasy_helper.categories import Category
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.context import LeagueContext, load_context
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.domain import LiveGame, Player, RosterEntry, StatLine
from fantasy_helper.ingest.run import IngestBusy
from fantasy_helper.ingest.run import run as run_ingest
from fantasy_helper.predict import WeekPredictions, predict_current_week
from fantasy_helper.trades import TradeIdeas, TradeReport, season_outlook, trade_ideas, trade_report
from fantasy_helper.waivers import WaiverReport, waiver_report

CACHE_SIZE = 32


class SnapshotCache:
    """A small LRU of computed results. Two requests for the same key at once may both
    compute it; the results are identical (seeded), so that only costs time."""

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self._items: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = compute()
        with self._lock:
            self._items[key] = value
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return value


router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


class Ctx:
    """Per-request access to settings, the cache and the current snapshot."""

    def __init__(self, request: Request, session: Annotated[Session, Depends(get_session)]):
        self.session = session
        self.settings: Settings = request.app.state.settings
        self.cache: SnapshotCache = request.app.state.cache
        league = session.get(m.League, self.settings.season)
        if league is None:
            raise LookupError(f"No league stored for season {self.settings.season}; run fh-ingest first")
        self.league = league
        self.key = (self.settings.season, league.updated_at)

    @property
    def season(self) -> int:
        return self.settings.season

    def context(self) -> LeagueContext:
        return self.cache.get(("context", self.key), lambda: load_context(self.session, self.season))

    def week(self) -> WeekPredictions:
        return self.cache.get(
            ("week", self.key), lambda: predict_current_week(self.session, self.season, ctx=self.context())
        )

    def outlook(self) -> Season:
        """Every team's remaining weeks as rostered now, for trades and power rankings."""
        return self.cache.get(("outlook", self.key), lambda: season_outlook(self.context()))


CtxDep = Annotated[Ctx, Depends()]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _ingest_out(run: m.IngestRun) -> s.IngestOut:
    return s.IngestOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        warnings=run.warnings or [],
    )


@router.get("/league", response_model=s.LeagueOut)
def league(c: CtxDep):
    ctx = c.context()
    run = c.session.scalars(select(m.IngestRun).order_by(m.IngestRun.id.desc()).limit(1)).first()
    return s.LeagueOut(
        season=c.season,
        name=c.league.name,
        my_team_id=c.settings.my_team_id,
        today=ctx.calendar.date_for(ctx.scoring_period),
        scoring_period=ctx.scoring_period,
        week=s.WeekOut(
            matchup_period=ctx.matchup_period,
            start=ctx.week.start,
            end=ctx.week.end,
            days=ctx.week.days,
            days_left=len(ctx.days_left),
        ),
        weeks=[
            s.WeekSpan(matchup_period=w.matchup_period, start=w.start, end=w.end)
            for w in ctx.calendar.weeks
            if w.matchup_period <= ctx.matchup_period
        ],
        categories=[s.CategoryOut(key=x.key, is_percentage=x.is_percentage, reverse=x.reverse) for x in ctx.categories],
        teams=[s.TeamOut(**vars(t)) for t in q.load_teams(c.session, c.season)],
        data_as_of=c.league.updated_at,
        last_ingest=_ingest_out(run) if run else None,
    )


def _past_period(ctx: LeagueContext, period: int | None) -> int | None:
    """An earlier week's matchup period, or None for the current week."""
    if period is None or period == ctx.matchup_period:
        return None
    if not 1 <= period < ctx.matchup_period:
        raise ValueError(f"Week {period} isn't played yet; weeks so far are 1-{ctx.matchup_period}")
    return period


def _score(results: dict[str, str]) -> tuple[int, int, int]:
    """Categories won, lost and tied."""
    counts = [list(results.values()).count(r) for r in ("win", "loss", "tie")]
    return counts[0], counts[1], counts[2]


def _outcome(won: int, lost: int) -> tuple[float, float, float]:
    """A finished week as odds: P(win), P(tie), P(loss) of 1 or 0."""
    return float(won > lost), float(won == lost), float(won < lost)


@router.get("/matchups", response_model=list[s.MatchupSummary])
def matchups(c: CtxDep, period: int | None = None):
    ctx = c.context()
    if (past := _past_period(ctx, period)) is not None:
        rows = []
        for mu in q.load_matchups(c.session, c.season, past):
            if mu.away_team_id is None:
                continue
            home, away, _ = _score(category_results(mu.home_cats, mu.away_cats, ctx.categories))
            p_home, p_tie, p_away = _outcome(home, away)
            rows.append(
                s.MatchupSummary(
                    espn_id=mu.espn_id,
                    home_team_id=mu.home_team_id,
                    away_team_id=mu.away_team_id,
                    p_home=p_home,
                    p_tie=p_tie,
                    p_away=p_away,
                    expected_home=home,
                    expected_away=away,
                    final=True,
                )
            )
        return rows
    return [
        s.MatchupSummary(
            espn_id=mu.espn_id,
            home_team_id=mu.home_team_id,
            away_team_id=mu.away_team_id,
            p_home=p.p_home,
            p_tie=p.p_tie,
            p_away=p.p_away,
            expected_home=p.expected_score["home"],
            expected_away=p.expected_score["away"],
        )
        for mu, p in c.week().results
    ]


def _history(c: Ctx, espn_id: int, side: str) -> list[s.OddsPoint]:
    """The odds stored by each ingest for one matchup, from one side, oldest first."""
    rows = c.session.scalars(
        select(m.MatchupPrediction)
        .where(m.MatchupPrediction.season == c.season, m.MatchupPrediction.espn_id == espn_id)
        .order_by(m.MatchupPrediction.id)
    )
    return [
        s.OddsPoint(
            created_at=r.created_at,
            scoring_period=r.scoring_period,
            p_win=r.p_home if side == "home" else r.p_away,
            p_tie=r.p_tie,
            p_loss=r.p_away if side == "home" else r.p_home,
        )
        for r in rows
    ]


@router.get("/matchup", response_model=s.MatchupDetail)
def matchup(c: CtxDep, team_id: int | None = None, period: int | None = None):
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    if (past := _past_period(ctx, period)) is not None:
        return _final_matchup(c, ctx, team_id, past)
    week = c.week()
    found = [(mu, p) for mu, p in week.results if team_id in (mu.home_team_id, mu.away_team_id)]
    if not found:
        raise LookupError(f"Team {team_id} has no opponent in matchup period {week.matchup_period}")
    mu, pred = found[0]
    side, other = ("home", "away") if mu.home_team_id == team_id else ("away", "home")
    view = side_view(pred, side)
    board = category_board(view.cat_p_win)
    now = {
        "home": category_values(ctx.actual(mu.home_team_id), ctx.categories),
        "away": category_values(ctx.actual(mu.away_team_id), ctx.categories),
    }
    history = _history(c, mu.espn_id, side)
    hw, aw, ties = pred.most_likely_score
    likely = {"home": hw, "away": aw}
    return s.MatchupDetail(
        espn_id=mu.espn_id,
        matchup_period=mu.matchup_period,
        team_id=team_id,
        opponent_id=mu.away_team_id if side == "home" else mu.home_team_id,
        week_days=[ctx.calendar.date_for(d) for d in week.week_days],
        days_left=[ctx.calendar.date_for(d) for d in week.days_left],
        p_win=view.p_win,
        p_tie=view.p_tie,
        p_loss=view.p_loss,
        expected=s.Score(
            me=pred.expected_score[side], opp=pred.expected_score[other], ties=pred.expected_score["ties"]
        ),
        most_likely=s.Score(me=likely[side], opp=likely[other], ties=ties),
        starts_left_me=pred.expected_games[side],
        starts_left_opp=pred.expected_games[other],
        categories=[
            s.CategoryOdds(
                key=cat.key,
                is_percentage=cat.is_percentage,
                reverse=cat.reverse,
                now_me=now[side].get(cat.key),
                now_opp=now[other].get(cat.key),
                proj_me=pred.projected_totals[side][cat.key],
                proj_opp=pred.projected_totals[other][cat.key],
                p_win=view.cat_p_win[cat.key],
                p_tie=pred.cat_probs[cat.key]["tie"],
                p_loss=view.cat_p_loss[cat.key],
                label=board[cat.key],
            )
            for cat in ctx.categories
        ],
        history=history,
        n_sims=pred.n_sims,
    )


def _final_matchup(c: Ctx, ctx: LeagueContext, team_id: int, period: int) -> s.MatchupDetail:
    """A finished week from one side: ESPN's final totals and each category's result."""
    found = [
        mu
        for mu in q.load_matchups(c.session, c.season, period)
        if mu.away_team_id is not None and team_id in (mu.home_team_id, mu.away_team_id)
    ]
    if not found:
        raise LookupError(f"Team {team_id} has no opponent in matchup period {period}")
    mu = found[0]
    side = "home" if mu.home_team_id == team_id else "away"
    mine, theirs = (mu.home_cats, mu.away_cats) if side == "home" else (mu.away_cats, mu.home_cats)
    results = category_results(mine, theirs, ctx.categories)
    won, lost, tied = _score(results)
    p_win, p_tie, p_loss = _outcome(won, lost)
    score = s.Score(me=won, opp=lost, ties=tied)
    return s.MatchupDetail(
        espn_id=mu.espn_id,
        matchup_period=period,
        team_id=team_id,
        opponent_id=mu.away_team_id if side == "home" else mu.home_team_id,
        week_days=[ctx.calendar.date_for(d) for d in ctx.calendar.week(period).scoring_periods],
        days_left=[],
        p_win=p_win,
        p_tie=p_tie,
        p_loss=p_loss,
        expected=score,
        most_likely=score,
        starts_left_me=0.0,
        starts_left_opp=0.0,
        categories=[
            s.CategoryOdds(
                key=cat.key,
                is_percentage=cat.is_percentage,
                reverse=cat.reverse,
                now_me=mine.get(cat.key),
                now_opp=theirs.get(cat.key),
                proj_me=mine.get(cat.key, 0.0),
                proj_opp=theirs.get(cat.key, 0.0),
                p_win=float(results[cat.key] == "win"),
                p_tie=float(results[cat.key] == "tie"),
                p_loss=float(results[cat.key] == "loss"),
                label=None,
                result=results[cat.key],
            )
            for cat in ctx.categories
        ],
        history=_history(c, mu.espn_id, side),
        n_sims=0,
        final=True,
    )


def _player(ctx: LeagueContext, pid: int | None, players: dict[int, Player] | None = None) -> s.PlayerOut | None:
    """`players` overrides ctx.players, e.g. for someone dropped earlier in the week."""
    if pid is None:
        return None
    p, z = (players or ctx.players)[pid], ctx.z.get(pid)
    return s.PlayerOut(
        player_id=pid,
        name=p.name,
        pro_team=p.pro_team,
        position=p.position,
        injury_status=p.injury_status,
        z=z.by_cat if z else {},
        value=z.value if z else 0.0,
    )


@router.get("/lineup", response_model=s.TeamLineup)
def lineup(c: CtxDep, team_id: int | None = None, period: int | None = None):
    """One team's week by player and day: box scores for days played, then projections
    that follow the lineup set on ESPN (not the simulation's best lineup). An earlier
    week (`period`) is box scores only."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    if not any(r.team_id == team_id for r in ctx.rosters):
        raise LookupError(f"No team {team_id} in season {c.season}")
    period = _past_period(ctx, period) or ctx.matchup_period
    days = list(ctx.calendar.week(period).scoring_periods)
    slots = {(d.player_id, d.scoring_period): d.lineup_slot for d in q.load_daily_lineups(c.session, c.season, team_id)}
    played_days = [d for d in days if d < ctx.scoring_period]
    played = q.load_game_stats(c.session, c.season, team_id=team_id, scoring_periods=played_days)
    ids = {pid for pid, _ in slots} | {g.player_id for g in played}
    players = ctx.players | q.load_players(c.session, ids - ctx.players.keys())
    schedule = q.load_schedule(c.session, c.season, days)
    # His NBA team's opponents; on days played, those of the team he played for then.
    opponents = {pid: dict(schedule.get(players[pid].pro_team, {})) for pid in ids if pid in players}
    for g in played:
        if g.player_id in opponents and (opp := schedule.get(g.pro_team or "", {}).get(g.scoring_period)):
            opponents[g.player_id][g.scoring_period] = opp
    week = team_week(
        days,
        ctx.scoring_period,
        ctx.slot_counts,
        slots,
        [g for g in played if g.player_id in players],
        {pid: ctx.sim_player(pid, ctx.days_left) for pid in ids if pid in ctx.rates and pid in ctx.players},
        opponents,
        live={pid: g for pid, g in ctx.live_stats.items() if pid in ids},
        games_today={
            pid: ctx.live_games[players[pid].pro_team]
            for pid in ids
            if pid in players and players[pid].pro_team in ctx.live_games
        },
    )
    return s.TeamLineup(
        team_id=team_id,
        matchup_period=period,
        days=[
            s.LineupDay(
                day=ctx.calendar.date_for(day),
                played=day < ctx.scoring_period,
                actual=week.day_actual[i],
                projected=week.day_projected[i],
                starts=week.starts[i],
                missed_starts=week.missed_starts[i],
            )
            for i, day in enumerate(days)
        ],
        players=[
            s.LineupPlayer(
                player=_player(ctx, row.player_id, players),
                cells=[
                    s.LineupCell(
                        slot=x.slot,
                        opponent=x.opponent,
                        counts=x.counts,
                        actual=x.actual,
                        stats=x.stats,
                        projected=x.projected,
                        game=_game(x.game),
                    )
                    for x in row.cells
                ],
                totals=row.totals,
            )
            for row in week.players
        ],
        totals=week.totals,
    )


def _game(g: LiveGame | None) -> s.GameOut | None:
    if g is None:
        return None
    if g.state == "final":
        status = "Final"
    elif g.state == "live" and g.period > 4:
        overtime = g.period - 4
        status = f"{'' if overtime == 1 else overtime}OT {g.clock}"
    elif g.state == "live":
        status = f"Q{g.period} {g.clock}" if g.period else "Live"
    else:
        status = ""
    return s.GameOut(state=g.state, status=status, tip_at=g.tip_at)


# Stat windows for the Team page; "model" is the blend of season and projection that predictions use.
WINDOWS = ["model", "season", "last_7", "last_15", "last_30", "projected"]
MOVES_SHOWN = 20


def _window(c: Ctx, ctx: LeagueContext, window: str) -> tuple[list[StatLine], dict[int, PlayerZ]]:
    """Every player's averages over one window, and z-scores against rostered players, valued
    with the model's G-score weights so every window weighs categories the same way."""
    if window == "model":
        games = {x.player_id: x for x in q.load_stat_lines(c.session, c.season, "projected")}
        games |= {x.player_id: x for x in q.load_stat_lines(c.session, c.season, "season")}
        lines = [
            StatLine(
                pid, "model", games[pid].gp if pid in games else 0.0, games[pid].mpg if pid in games else 0.0, rates
            )
            for pid, rates in ctx.rates.items()
        ]
        return lines, ctx.z
    if window not in WINDOWS:
        raise ValueError(f"Unknown window {window!r}; one of {', '.join(WINDOWS)}")
    lines = q.load_stat_lines(c.session, c.season, window)
    rostered = {r.player_id for r in ctx.rosters}
    return lines, player_zscores(lines, ctx.categories, baseline_ids=rostered, weights=ctx.g_weights)


@router.get("/team", response_model=s.TeamDetail)
def team(c: CtxDep, team_id: int | None = None, window: str = "model"):
    """One team's roster with per-game averages and z-scores over a stat window, its
    category profile against the league (team totals, ranks, punts), and its latest moves."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    entries = [r for r in ctx.rosters if r.team_id == team_id]
    teams = {t.team_id: t for t in q.load_teams(c.session, c.season)}
    if not entries or team_id not in teams:
        raise LookupError(f"No team {team_id} in season {c.season}")
    lines, z = c.cache.get(("window", c.key, window), lambda: _window(c, ctx, window))
    profile = team_profiles(ctx.rosters, lines, ctx.categories)[team_id]
    by_player = {x.player_id: x for x in lines}

    def order(r: RosterEntry) -> tuple[int, float]:
        rank = SLOT_ORDER.index(r.lineup_slot) if r.lineup_slot in SLOT_ORDER else len(SLOT_ORDER)
        return rank, -(z[r.player_id].value if r.player_id in z else 0.0)

    players = []
    for r in sorted(entries, key=order):
        line, pz = by_player.get(r.player_id), z.get(r.player_id)
        by_cat = pz.by_cat if pz else {}
        players.append(
            s.TeamPlayer(
                player=_player(ctx, r.player_id),
                slot=r.lineup_slot,
                acquisition_type=r.acquisition_type,
                acquired_at=r.acquired_at,
                gp=line.gp if line else None,
                mpg=line.mpg if line else None,
                per_game=line.per_game if line else None,
                z=by_cat,
                value=pz.value if pz else 0.0,
                specialties=specialties(by_cat),
            )
        )
    moves = [
        s.MoveOut(at=x.at, type=x.type, kind=x.kind, player_id=x.player_id, player_name=x.player_name)
        for x in q.load_moves(c.session, c.season, team_id, MOVES_SHOWN)
    ] + [
        s.MoveOut(
            at=r.acquired_at,
            type="TRADE",
            kind="TRADE",
            player_id=r.player_id,
            player_name=ctx.players[r.player_id].name,
        )
        for r in entries
        if r.acquisition_type == "TRADE"
    ]
    moves.sort(key=lambda x: x.at.timestamp() if x.at else 0.0, reverse=True)
    return s.TeamDetail(
        team=s.TeamOut(**vars(teams[team_id])),
        window=window,
        windows=WINDOWS,
        profile=_profile_out(ctx.categories, profile),
        punts=profile.punts,
        players=players,
        moves=moves[:MOVES_SHOWN],
        notes=[
            "Team values are the sum of the roster's per-game averages (IR excluded); FG% and FT% come from "
            "summed makes and attempts.",
            f"A punt is a category where the team sits more than {-DEFAULT_PUNT_Z:g} standard deviations below "
            "the league's teams.",
            f"Specialist tags mark categories a player is at least {SPECIALTY_Z:g} standard deviation above the "
            "average rostered player in.",
            "Trades show the players this team received and still has; ESPN lists trades without their players "
            "unless signed in.",
        ],
    )


def _profile_out(cats: list[Category], profile: TeamProfile) -> list[s.CategoryProfile]:
    return [
        s.CategoryProfile(
            key=cat.key,
            value=profile.totals[cat.key],
            rank=profile.ranks[cat.key],
            z=profile.league_z[cat.key],
            punt=cat.key in profile.punts,
        )
        for cat in cats
    ]


@router.get("/profiles", response_model=s.ProfilesOut)
def profiles(c: CtxDep, window: str = "model"):
    """Every team's category profile over one stat window: the league heatmap."""
    ctx = c.context()
    lines, _ = c.cache.get(("window", c.key, window), lambda: _window(c, ctx, window))
    by_team = team_profiles(ctx.rosters, lines, ctx.categories)
    return s.ProfilesOut(
        window=window,
        windows=WINDOWS,
        teams=[
            s.TeamProfileOut(
                team=s.TeamOut(**vars(t)),
                profile=_profile_out(ctx.categories, by_team[t.team_id]),
                punts=by_team[t.team_id].punts,
            )
            for t in q.load_teams(c.session, c.season)
            if t.team_id in by_team
        ],
        notes=[
            "Each cell is the team's per-game value in that category (the sum of its players' averages, IR excluded), "
            "shaded by how far it sits from the league's teams and ranked 1 to 10.",
            f"A punt is a category more than {-DEFAULT_PUNT_Z:g} standard deviations below the league's teams.",
        ],
    )


@router.get("/power", response_model=s.PowerOut)
def power(c: CtxDep):
    """Power rankings: rest-of-season strength against every team, with the all-play record so far."""
    ctx, outlook = c.context(), c.outlook()
    completed = [mu for p in range(1, ctx.matchup_period) for mu in q.load_matchups(c.session, c.season, p)]
    rows = power_rankings(ctx.categories, outlook.weekly, ctx.dispersion, all_play_records(completed, ctx.categories))
    lines, _ = c.cache.get(("window", c.key, "model"), lambda: _window(c, ctx, "model"))
    punts = {t: p.punts for t, p in team_profiles(ctx.rosters, lines, ctx.categories).items()}
    teams = {t.team_id: t for t in q.load_teams(c.session, c.season)}
    later = ctx.later_weeks
    return s.PowerOut(
        weeks=len(later),
        first_period=later[0].matchup_period,
        completed_weeks=ctx.matchup_period - 1,
        rows=[
            s.PowerRow(
                team=s.TeamOut(**vars(teams[r.team_id])),
                rank=r.rank,
                strength=r.strength,
                cats_per_week=r.cats_per_week,
                cat_p_win=r.cat_p_win,
                all_play=s.RecordOut(**vars(r.all_play)),
                punts=punts.get(r.team_id, []),
            )
            for r in rows
        ],
        notes=[
            f"Strength is the expected share of weekly matchups a team would win playing every other team in each of "
            f"weeks {later[0].matchup_period}-{later[-1].matchup_period} (playoffs included), with rosters as they "
            "are now and everyone healthy; a tied week counts half.",
            "Strength counts each team's NBA schedule and the daily lineup cap, so it can disagree with the per-game "
            "heatmap. Winning a week treats the nine categories as independent.",
            "All-play so far compares each completed week's totals with every other team's that week, so it doesn't "
            "depend on who a team happened to play.",
        ],
    )


def _side_out(ctx: LeagueContext, side: TradeSide) -> s.TradeSideOut:
    def players(ids: list[int]) -> list[s.PlayerOut]:
        return [_player(ctx, pid) for pid in ids]

    return s.TradeSideOut(
        team_id=side.team_id,
        receives=players(side.receives),
        sends=players(side.sends),
        drops=players(side.drops),
        adds=players(side.adds),
        cats_per_week=side.cats_per_week,
        d_cats_per_week=side.d_cats_per_week,
        p_week=side.p_week,
        d_p_week=side.d_p_week,
        cat_p_win=side.cat_p_win,
        cat_d_p_win=side.cat_d_p_win,
        cat_d_value=side.cat_d_value,
    )


@router.get("/trade", response_model=s.TradeOut)
def trade(
    c: CtxDep,
    partner_id: int,
    give: Annotated[list[int], Query()],
    get: Annotated[list[int], Query()],
    team_id: int | None = None,
):
    """What a deal does for both teams: `give` are my players, `get` the partner's."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    report: TradeReport = c.cache.get(
        ("trade", c.key, team_id, partner_id, tuple(sorted(give)), tuple(sorted(get))),
        lambda: trade_report(ctx, c.outlook(), team_id, partner_id, give, get),
    )
    later = ctx.later_weeks
    return s.TradeOut(
        me=_side_out(ctx, report.result.me),
        partner=_side_out(ctx, report.result.partner),
        weeks=len(later),
        first_period=later[0].matchup_period,
        warnings=report.warnings,
        assumptions=report.assumptions,
    )


@router.get("/trades", response_model=s.TradesOut)
def trades(c: CtxDep, team_id: int | None = None):
    """Deals that help both teams, found by searching 1-for-1 and 2-for-1 trades with every other team."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    ideas: TradeIdeas = c.cache.get(("trades", c.key, team_id), lambda: trade_ideas(ctx, c.outlook(), team_id))
    later = ctx.later_weeks
    return s.TradesOut(
        team_id=team_id,
        deals=[s.TradeIdea(me=_side_out(ctx, d.me), partner=_side_out(ctx, d.partner)) for d in ideas.search.deals],
        candidates=ideas.search.candidates,
        evaluated=ideas.search.evaluated,
        weeks=len(later),
        first_period=later[0].matchup_period,
        assumptions=ideas.assumptions,
    )


def _recent_ingest(state: Any) -> m.IngestRun | None:
    """The last successful ingest, if it finished within the refresh cooldown."""
    cooldown = state.settings.refresh_cooldown_seconds
    if cooldown <= 0:
        return None
    with state.sessionmaker() as session:
        run = session.scalars(
            select(m.IngestRun)
            .where(m.IngestRun.season == state.settings.season, m.IngestRun.status == "success")
            .order_by(m.IngestRun.id.desc())
            .limit(1)
        ).first()
    if run is None or run.finished_at is None:
        return None
    finished = run.finished_at if run.finished_at.tzinfo else run.finished_at.replace(tzinfo=UTC)  # SQLite: naive UTC
    return run if datetime.now(UTC) - finished < timedelta(seconds=cooldown) else None


@router.post("/refresh", response_model=s.IngestOut)
def refresh(request: Request):
    """Pull a fresh snapshot from ESPN now: the same job as `fh-ingest`, one at a time.
    Cached results are keyed by the snapshot, so the next reads recompute. Within the
    cooldown after a successful ingest, that run is returned instead of fetching again."""
    state = request.app.state
    if recent := _recent_ingest(state):
        return _ingest_out(recent)
    if not state.refresh_lock.acquire(blocking=False):
        return JSONResponse(status_code=409, content={"detail": "A refresh is already running"})
    try:
        run_id = run_ingest(state.settings, state.sessionmaker).id
    except IngestBusy:  # the scheduled ingest, or a refresh in another process
        return JSONResponse(status_code=409, content={"detail": "A refresh is already running"})
    except Exception as exc:  # the run row records the failure; the client just needs the reason
        return JSONResponse(status_code=502, content={"detail": f"Refresh from ESPN failed: {exc}"})
    finally:
        state.refresh_lock.release()
    with state.sessionmaker() as session:  # re-read: predictions may have added warnings
        return _ingest_out(session.get(m.IngestRun, run_id))


@router.get("/waivers", response_model=s.WaiversOut)
def waivers(c: CtxDep, team_id: int | None = None, same_day: bool = False):
    """`same_day`: rank as if a pickup can play today, not from tomorrow."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    report: WaiverReport = c.cache.get(
        ("waivers", c.key, team_id, same_day),
        lambda: waiver_report(c.session, c.season, team_id, same_day=same_day, ctx=ctx),
    )
    base, date_for = report.stream.baseline, ctx.calendar.date_for
    return s.WaiversOut(
        team_id=team_id,
        opponent_id=report.opponent_id,
        matchup_period=report.matchup_period,
        first_day=date_for(report.first_day),
        days_left=[date_for(d) for d in report.days_left],
        baseline=s.Baseline(
            p_win=base.p_win,
            p_tie=base.p_tie,
            p_loss=base.p_loss,
            expected_cats=base.expected_cats,
            starts_left=base.expected_games,
        ),
        board=[
            s.BoardEntry(
                key=k,
                label=label,
                p_win=base.cat_p_win[k],
                p_tie=1 - base.cat_p_win[k] - base.cat_p_loss[k],
                p_loss=base.cat_p_loss[k],
            )
            for k, label in report.stream.board.items()
        ],
        stream=[
            s.StreamRow(
                add=_player(ctx, o.add_id),
                drop=_player(ctx, o.drop_id),
                games=report.games_this_week[o.add_id],
                extra_starts=o.extra_starts,
                p_win=o.result.p_win,
                d_p_win=o.d_p_win,
                d_p_tie=o.d_p_tie,
                d_expected_cats=o.d_expected_cats,
                cat_d_p_win=o.cat_d_p_win,
                gained=o.gained,
                lost=o.lost,
            )
            for o in report.stream.options
        ],
        stream_screened=report.stream.screened,
        hold=s.HoldOut(
            weeks=report.hold.weeks,
            first_period=report.hold_periods[0] if report.hold_periods else None,
            cats_per_week=report.hold.cats_per_week,
            cat_p_win=report.hold.cat_p_win,
            rows=[
                s.HoldRow(
                    add=_player(ctx, o.add_id),
                    drop=_player(ctx, o.drop_id),
                    games=report.hold_games[o.add_id],
                    d_cats_per_week=o.d_cats_per_week,
                    cat_d_p_win=o.cat_d_p_win,
                )
                for o in report.hold.options
            ],
            screened=report.hold.screened,
        ),
        drops=[_player(ctx, d) for d in report.drop_ids],
        free_agents=report.free_agents,
        assumptions=report.assumptions,
        n_sims=report.n_sims,
    )


def create_app(session_factory: sessionmaker | None = None, settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Fantasy Helper", version="0.1.0")
    app.state.sessionmaker = session_factory or get_sessionmaker()
    app.state.settings = settings or get_settings()
    app.state.cache = SnapshotCache()
    app.state.refresh_lock = threading.Lock()
    app.include_router(router)

    @app.exception_handler(LookupError)
    def not_found(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    def bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Fantasy Helper REST API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args()
    uvicorn.run("fantasy_helper.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
