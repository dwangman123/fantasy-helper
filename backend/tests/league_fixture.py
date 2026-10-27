"""A synthetic league for service and API tests: 10 teams, a free-agent pool and a
full-season NBA schedule, shaped like the real league but random (seeded)."""

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fantasy_helper.analytics.actuals import week_actuals
from fantasy_helper.calendar import load_calendar
from fantasy_helper.categories import KNOWN_CATEGORIES, categories_from_scoring_items
from fantasy_helper.db import models as m
from fantasy_helper.domain import (
    DailySlot,
    LeagueSnapshot,
    LiveGame,
    Matchup,
    Player,
    PlayerGameStat,
    ProGame,
    RosterEntry,
    StatLine,
    Team,
)
from fantasy_helper.ingest.store import store_snapshot

SEASON = 2027
MY_TEAM = 4
SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1}
NBA = (
    "ATL BKN BOS CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM "
    "MIA MIL MIN NOP NYK OKC ORL PHL PHO POR SAC SAS TOR UTA WAS"
).split()
POSITIONS = {
    "PG": ["PG", "G", "UT"],
    "SG": ["SG", "G", "UT"],
    "SF": ["SF", "F", "UT"],
    "PF": ["PF", "F", "UT"],
    "C": ["C", "UT"],
}
# Per-game rates for an average rostered player, by position.
BASE = {
    "PG": {"PTS": 17, "REB": 4, "AST": 6.5, "STL": 1.2, "BLK": 0.3, "3PM": 2.3, "TO": 2.6, "FGA": 14, "FTA": 3.5},
    "SG": {"PTS": 16, "REB": 4, "AST": 3.5, "STL": 1.0, "BLK": 0.4, "3PM": 2.4, "TO": 1.8, "FGA": 13, "FTA": 3.0},
    "SF": {"PTS": 15, "REB": 5.5, "AST": 3, "STL": 1.0, "BLK": 0.6, "3PM": 1.8, "TO": 1.6, "FGA": 12, "FTA": 3.0},
    "PF": {"PTS": 14, "REB": 7.5, "AST": 2.5, "STL": 0.8, "BLK": 0.9, "3PM": 1.2, "TO": 1.6, "FGA": 11, "FTA": 3.2},
    "C": {"PTS": 13, "REB": 10, "AST": 2.2, "STL": 0.7, "BLK": 1.6, "3PM": 0.4, "TO": 1.8, "FGA": 9.5, "FTA": 3.5},
}
FG_PCT = {"PG": 0.45, "SG": 0.45, "SF": 0.47, "PF": 0.50, "C": 0.56}
FT_PCT = {"PG": 0.84, "SG": 0.82, "SF": 0.79, "PF": 0.75, "C": 0.68}


def _per_game(rng: np.random.Generator, position: str, scale: float) -> dict[str, float]:
    rates = {k: v * scale * rng.uniform(0.6, 1.4) for k, v in BASE[position].items()}
    rates["FGM"] = rates["FGA"] * min(FG_PCT[position] + rng.normal(0, 0.03), 0.7)
    rates["FTM"] = rates["FTA"] * min(FT_PCT[position] + rng.normal(0, 0.05), 0.95)
    return rates


def league_snapshot(matchup_period: int = 3, day_of_week: int = 3, n_free_agents: int = 60, seed: int = 0):
    """The league on day `day_of_week` (0-based) of matchup period `matchup_period`."""
    rng = np.random.default_rng(seed)
    calendar = load_calendar(SEASON)
    week = calendar.week(matchup_period)
    today = week.first_scoring_period + day_of_week

    pro_games = []
    for day in range(1, calendar.weeks[-1].last_scoring_period + 1):
        teams = rng.permutation(NBA)[: 2 * int(rng.integers(3, 13))]
        for a, b in zip(teams[::2], teams[1::2], strict=True):
            pro_games += [ProGame(a, day, b, calendar.date_for(day)), ProGame(b, day, a, calendar.date_for(day))]

    players, lines, rosters = [], [], []

    def add_player(scale: float, team_id: int | None = None, slot: str = "BE", status: str = "ACTIVE") -> int:
        pid = 1000 + len(players)
        position = str(rng.choice(list(POSITIONS)))
        players.append(Player(pid, f"Player {pid}", str(rng.choice(NBA)), position, POSITIONS[position], status))
        per_game = _per_game(rng, position, scale)
        gp = float(week.first_scoring_period // 2)
        lines.append(StatLine(pid, "projected", 70.0, 30.0, per_game))
        if gp:
            lines.append(
                StatLine(pid, "season", gp, 30.0, {k: v * rng.uniform(0.85, 1.15) for k, v in per_game.items()})
            )
        if team_id is not None:
            rosters.append(RosterEntry(team_id, pid, slot, "DRAFT"))
        return pid

    for team_id in range(1, 11):
        for i in range(13):
            status = "DAY_TO_DAY" if rng.random() < 0.08 else "ACTIVE"
            add_player(rng.uniform(0.8, 1.3), team_id, "UT" if i < 10 else "BE", status)
        add_player(1.0, team_id, "IR", "OUT")
    for _ in range(n_free_agents):
        add_player(rng.uniform(0.5, 0.9), status="OUT" if rng.random() < 0.05 else "ACTIVE")

    # Box scores on the days already played this week: each player's expected stats, a little low.
    pro_team = {p.player_id: p.pro_team for p in players}
    playing = {(g.pro_team, g.scoring_period) for g in pro_games}
    projected = {s.player_id: s.per_game for s in lines if s.window == "projected"}

    def box(r: RosterEntry, day: int, share: float) -> PlayerGameStat:
        stats = {k: float(round(v * share)) for k, v in projected[r.player_id].items()}
        return PlayerGameStat(r.player_id, day, pro_team[r.player_id], 30.0 * share, stats, r.team_id, r.lineup_slot)

    game_stats = [
        box(r, day, 0.8)
        for day in range(week.first_scoring_period, today)
        for r in rosters
        if r.lineup_slot != "IR" and (pro_team[r.player_id], day) in playing
    ]
    # Today: a third of the NBA teams playing are final, a third live (3rd quarter, 6:00 left), the rest to come.
    teams_today = sorted(g.pro_team for g in pro_games if g.scoring_period == today)
    third = len(teams_today) // 3
    states = [("final", 4, "0.0", 0.0), ("live", 3, "6:00", 0.375), ("pre", 0, "", 1.0)]
    live_games = [
        LiveGame(t, datetime(2026, 1, 1, tzinfo=UTC), *states[min(i // third, 2)]) for i, t in enumerate(teams_today)
    ]
    shares = {g.pro_team: 1.0 if g.state == "final" else 0.6 for g in live_games if g.state != "pre"}
    live_stats = [
        box(r, today, shares[pro_team[r.player_id]])
        for r in rosters
        if r.lineup_slot != "IR" and pro_team[r.player_id] in shares
    ]

    # ESPN's matchup totals, which match the completed days' started box scores.
    def espn_totals(team_id: int) -> dict[str, float]:
        totals = week_actuals(game_stats).get(team_id, {})
        return {**totals, "FG%": totals["FGM"] / totals["FGA"], "FT%": totals["FTM"] / totals["FTA"]} if totals else {}

    pairs = [(MY_TEAM, 7), (1, 2), (3, 5), (6, 8), (9, 10)]
    matchups = [
        Matchup(100 * matchup_period + i, matchup_period, h, a, "UNDECIDED", "NONE", espn_totals(h), espn_totals(a))
        for i, (h, a) in enumerate(pairs)
    ]
    return LeagueSnapshot(
        league_id=1,
        season=SEASON,
        name="Synthetic League",
        categories=categories_from_scoring_items(
            [{"statId": c.espn_stat_id, "isReverseItem": c.reverse} for c in KNOWN_CATEGORIES.values()]
        ),
        current_matchup_period=matchup_period,
        current_scoring_period=today,
        teams=[Team(t, f"Team {t}", f"T{t}", 1, 1, 0) for t in range(1, 11)],
        players=players,
        rosters=rosters,
        stat_lines=lines,
        pro_games=pro_games,
        matchups=matchups,
        matchup_ids={},
        fetched_at=datetime.now(UTC),
        lineup_slot_counts=SLOTS,
        scoring_type="H2H_MOST_CATEGORIES",
        # Each set lineup is today's roster slot, carried through the week.
        daily_lineups=[
            DailySlot(r.team_id, r.player_id, day, r.lineup_slot)
            for day in week.scoring_periods
            if day >= today
            for r in rosters
        ],
        game_stats=game_stats,
        live_stats=live_stats,
        live_games=live_games,
    )


def stored_league(url: str = "sqlite://", **kw):
    """An engine with the synthetic league stored."""
    engine = create_engine(url)
    m.Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        store_snapshot(s, league_snapshot(**kw))
    return engine
