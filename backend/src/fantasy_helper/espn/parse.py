"""Pure conversions from espn_api objects / raw ESPN JSON into domain types.

No network access here, so everything is unit-testable with fakes.
"""

from typing import Any

from fantasy_helper.calendar import MatchupCalendar
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS, STAT_WINDOWS, Matchup, Player, ProGame, RosterEntry, StatLine, Team


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


def _cats_from_score(side: dict, categories: list[Category]) -> dict[str, float]:
    by_stat = (side.get("cumulativeScore") or {}).get("scoreByStat") or {}
    out = {}
    for cat in categories:
        entry = by_stat.get(str(cat.espn_stat_id))
        if entry and entry.get("score") is not None:
            out[cat.key] = float(entry["score"])
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
