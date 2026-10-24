"""Pure conversions from espn_api objects / raw ESPN JSON into domain types.

No network access here, so everything is unit-testable with fakes.
"""

from datetime import UTC, datetime
from typing import Any

from espn_api.basketball.constant import POSITION_MAP, PRO_TEAM_MAP, STATS_MAP

from fantasy_helper.calendar import MatchupCalendar
from fantasy_helper.categories import COMPONENT_STAT_IDS, Category
from fantasy_helper.domain import (
    STAT_KEYS,
    STAT_WINDOWS,
    Matchup,
    OpponentStats,
    Player,
    PlayerGameStat,
    ProGame,
    RosterEntry,
    StatLine,
    Team,
)

# Raw ESPN stat entries: statSourceId 0 = actual (1 = projected); statSplitTypeId 5 = one day.
_ACTUAL, _ONE_DAY = 0, 5


def parse_player(p: Any) -> Player:
    return Player(
        player_id=int(p.playerId),
        name=p.name,
        pro_team=p.proTeam,
        position=p.position,
        eligible_slots=[s for s in p.eligibleSlots if s not in ("BE", "IR")],
        injury_status=p.injuryStatus,
    )


def parse_stat_lines(p: Any, season: int) -> list[StatLine]:
    """One StatLine per stat window that has games (or a projection) behind it."""
    lines = []
    for suffix, window in STAT_WINDOWS.items():
        block = p.stats.get(f"{season}_{suffix}") or {}
        avg = block.get("avg")
        if not avg:
            continue
        total = block.get("total") or {}
        gp = float(total.get("GP") or avg.get("GP") or 0)
        if gp <= 0:
            continue
        lines.append(
            StatLine(
                player_id=int(p.playerId),
                window=window,
                gp=gp,
                mpg=float(avg.get("MPG") or avg.get("MIN") or 0),
                per_game={key: float(avg.get(key) or 0) for key in STAT_KEYS},
            )
        )
    return lines


def parse_team(t: Any) -> Team:
    return Team(
        team_id=int(t.team_id),
        name=t.team_name,
        abbrev=t.team_abbrev,
        wins=int(t.wins),
        losses=int(t.losses),
        ties=int(getattr(t, "ties", 0) or 0),
    )


def parse_roster(t: Any) -> list[RosterEntry]:
    return [
        RosterEntry(
            team_id=int(t.team_id),
            player_id=int(p.playerId),
            lineup_slot=p.lineupSlot,
            acquisition_type=p.acquisitionType or None,
        )
        for p in t.roster
    ]


def parse_pro_games(p: Any, calendar: MatchupCalendar) -> list[ProGame]:
    """A player's schedule is their NBA team's schedule, keyed by scoring period.

    Dates come from the scoring period rather than the game timestamp, which
    espn_api converts to the machine's local timezone.
    """
    if p.proTeam in (None, "", "FA"):
        return []
    return [
        ProGame(
            pro_team=p.proTeam,
            scoring_period=int(sp),
            opponent=game["team"],
            game_date=calendar.date_for(int(sp)),
        )
        for sp, game in p.schedule.items()
    ]


def parse_lineup_slot_counts(roster_settings: dict) -> dict[str, int]:
    """Slot name -> count from mSettings rosterSettings.lineupSlotCounts; unused slots dropped."""
    return {
        POSITION_MAP[int(slot_id)]: int(count)
        for slot_id, count in roster_settings["lineupSlotCounts"].items()
        if int(count) > 0
    }


def first_tip(players: list[Any], scoring_period: int) -> datetime | None:
    """Earliest game start on a scoring period, from player schedules (as UTC).

    espn_api gives naive datetimes in the machine's local timezone.
    """
    tips = [g["date"] for p in players for sp, g in p.schedule.items() if int(sp) == scoring_period and g.get("date")]
    return min(tips).astimezone(UTC) if tips else None


def _cats_from_score(side: dict, categories: list[Category]) -> dict[str, float]:
    """Category totals plus the makes/attempts components (FGM, FGA, FTM, FTA)."""
    by_stat = (side.get("cumulativeScore") or {}).get("scoreByStat") or {}
    keys = {cat.espn_stat_id: cat.key for cat in categories} | COMPONENT_STAT_IDS
    out = {}
    for stat_id, key in keys.items():
        entry = by_stat.get(str(stat_id))
        if entry and entry.get("score") is not None:
            out[key] = float(entry["score"])
    return out


def parse_matchups(schedule: list[dict], categories: list[Category]) -> list[Matchup]:
    """Parse the raw `schedule` array from the mMatchup view."""
    matchups = []
    for m in schedule:
        home, away = m.get("home") or {}, m.get("away")
        matchups.append(
            Matchup(
                espn_id=int(m["id"]),
                matchup_period=int(m["matchupPeriodId"]),
                home_team_id=int(home["teamId"]),
                away_team_id=int(away["teamId"]) if away else None,
                winner=m.get("winner", "UNDECIDED"),
                playoff_tier=m.get("playoffTierType", "NONE"),
                home_cats=_cats_from_score(home, categories),
                away_cats=_cats_from_score(away, categories) if away else {},
            )
        )
    return matchups


def parse_game_stat(
    player: dict, scoring_period: int, team_id: int | None = None, lineup_slot_id: int | None = None
) -> PlayerGameStat | None:
    """A player's box score for one day from raw ESPN player JSON; None if he didn't play."""
    for entry in player.get("stats") or []:
        if (entry.get("statSourceId"), entry.get("statSplitTypeId"), entry.get("scoringPeriodId")) != (
            _ACTUAL,
            _ONE_DAY,
            scoring_period,
        ):
            continue
        raw = {STATS_MAP.get(k, k): v for k, v in (entry.get("stats") or {}).items()}
        minutes = float(raw.get("MIN") or 0)
        if minutes <= 0:
            return None
        return PlayerGameStat(
            player_id=int(player["id"]),
            scoring_period=scoring_period,
            pro_team=PRO_TEAM_MAP.get(entry.get("proTeamId")),
            minutes=minutes,
            stats={key: float(raw.get(key) or 0) for key in STAT_KEYS},
            team_id=team_id,
            lineup_slot=POSITION_MAP.get(lineup_slot_id) if lineup_slot_id is not None else None,
        )
    return None


# ESPN's public site API abbreviates 8 teams differently from the fantasy API.
SITE_TO_FANTASY_TEAM = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "PHI": "PHL",
    "PHX": "PHO",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

# Public site API stat names -> our stat keys, read from the "Opponent ..." blocks.
_OPPONENT_STAT_NAMES = {
    "avgPoints": "PTS",
    "avgRebounds": "REB",
    "avgAssists": "AST",
    "avgSteals": "STL",
    "avgBlocks": "BLK",
    "avgThreePointFieldGoalsMade": "3PM",
    "avgTurnovers": "TO",
    "avgFieldGoalsMade": "FGM",
    "avgFieldGoalsAttempted": "FGA",
    "avgFreeThrowsMade": "FTM",
    "avgFreeThrowsAttempted": "FTA",
}


def parse_opponent_stats(data: dict) -> list[OpponentStats]:
    """Per-team stats allowed, from ESPN's statistics/byteam?category=opponent response.

    Each team has paired category blocks, "Own ..." and "Opponent ..."; values line up
    with the names in the top-level `categories` list of the same name.
    """
    names = {c["name"]: c["names"] for c in data["categories"] if c.get("names")}
    out = []
    for team in data["teams"]:
        values: dict[str, float] = {}
        for block in team["categories"]:
            if block.get("displayName", "").startswith("Opponent") and block["name"] in names:
                values |= dict(zip(names[block["name"]], block["values"], strict=False))
        abbrev = team["team"]["abbreviation"]
        out.append(
            OpponentStats(
                pro_team=SITE_TO_FANTASY_TEAM.get(abbrev, abbrev),
                gp=float(values.get("gamesPlayed") or 0),
                per_game={key: float(values[name]) for name, key in _OPPONENT_STAT_NAMES.items()},
            )
        )
    return out
