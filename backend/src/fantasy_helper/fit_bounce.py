"""Fit the weekly bounce behind the G-score weights from last season's game logs;
`fh-fit-bounce` writes it to `data/weekly_bounce_<season>.json` (see `analytics/zscores.py`).

A category's weekly bounce (tau^2) is how much a typical player's per-game average moves
from week to week: for each player in the pool, the sample variance of his per-game value
over the Monday-Sunday weeks in which he played at least `min_week_games` games, averaged
over the pool. Per-game averages leave out 2-game vs 4-game schedule swings, which the
models already know; the games-played floor leaves out short weeks (opening week, the
All-Star break) and missed games, which are availability, not bounce. Percentage cats use
the same volume-weighted impact as the z-scores, against the pool's league rate.

The pool is last season's top `pool_size` players by z-sum among those with at least
`min_games` games, out of the `candidates` most-owned players in ESPN fantasy at the end
of that season. Game logs come from ESPN's public NBA site API, one request per player.
A log dates each game by its UTC tip-off, a day late for most evening games, which would
push Sunday-night games into the next week, so each game's US date is taken from that
day's scoreboard instead. `--cache` keeps the raw responses so a rerun doesn't fetch again.

Also stored, for comparison only: `single_game_var`, each player's per-game variance over
the same weeks scaled to a week as if his games were independent (`weekly_var` over it is
how much a week's games move together), and the pool's mean and spread. At runtime only
`weekly_var` and `pool_mean` are used, against this season's spread.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from math import sqrt
from pathlib import Path
from statistics import fmean, variance

from fantasy_helper.analytics.zscores import baselines, bounce_path, player_zscores, raw_value
from fantasy_helper.categories import KNOWN_CATEGORIES, Category
from fantasy_helper.config import get_settings
from fantasy_helper.domain import STAT_KEYS, StatLine
from fantasy_helper.espn.client import SCOREBOARD_URL

PLAYERS_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
    "/segments/0/leaguedefaults/3?view=kona_player_info"
)
GAMELOG_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{player_id}/gamelog?season={season}"
)
USER_AGENT = "fantasy-helper"
REQUEST_GAP = 0.1  # seconds between requests that aren't cached

CANDIDATES, MIN_GAMES, POOL_SIZE, MIN_WEEK_GAMES, MIN_WEEKS = 300, 20, 130, 3, 4

# ESPN game-log stat names -> our stat keys. "made-attempted" pairs are split.
_PAIRS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("FGM", "FGA"),
    "freeThrowsMade-freeThrowsAttempted": ("FTM", "FTA"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("3PM", None),
}
_SINGLES = {
    "totalRebounds": "REB",
    "assists": "AST",
    "steals": "STL",
    "blocks": "BLK",
    "turnovers": "TO",
    "points": "PTS",
}


@dataclass(frozen=True)
class Game:
    day: date
    stats: dict[str, float]  # keyed by STAT_KEYS


@dataclass(frozen=True)
class CategoryBounce:
    weekly_var: float
    single_game_var: float
    pool_mean: float
    pool_sd: float


def nine_categories() -> list[Category]:
    return list(KNOWN_CATEGORIES.values())


def _num(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def parse_gamelog(raw: dict, game_days: dict[str, date] | None = None) -> list[Game]:
    """The regular-season games he played, oldest first, from ESPN's athlete gamelog JSON.

    The log's `gameDate` is the UTC date of tip-off, a day late for most evening games, so
    each game's US date comes from `game_days` (event id -> scoreboard day) when it's there."""
    names, events = raw.get("names") or [], raw.get("events") or {}
    game_days = game_days or {}
    games = []
    for season_type in raw.get("seasonTypes") or []:
        if "Regular Season" not in (season_type.get("displayName") or ""):
            continue
        for month in season_type.get("categories") or []:
            for row in month.get("events") or []:
                values = dict(zip(names, row.get("stats") or [], strict=False))
                event = events.get(row.get("eventId"))
                if event is None or _num(values.get("minutes")) <= 0:
                    continue
                stats = dict.fromkeys(STAT_KEYS, 0.0)
                for name, (made, attempted) in _PAIRS.items():
                    m, _, a = (values.get(name) or "0-0").partition("-")
                    stats[made] = _num(m)
                    if attempted:
                        stats[attempted] = _num(a)
                for name, key in _SINGLES.items():
                    stats[key] = _num(values.get(name))
                day = game_days.get(row["eventId"]) or utc_day(event)
                games.append(Game(day, stats))
    return sorted(games, key=lambda g: g.day)


def utc_day(event: dict) -> date:
    return date.fromisoformat(event["gameDate"][:10])


def log_days(raw: dict) -> list[date]:
    """The UTC dates of every game in a raw gamelog, to bound the scoreboard days to fetch."""
    return [utc_day(e) for e in (raw.get("events") or {}).values() if e.get("gameDate")]


def parse_scoreboard(raw: dict, day: date) -> dict[str, date]:
    """Event id -> `day` for every game on one day's scoreboard (ESPN dates it by US day)."""
    return {str(e["id"]): day for e in raw.get("events") or []}


def season_lines(logs: dict[int, list[Game]], min_games: int) -> list[StatLine]:
    """Per-game averages for players with at least `min_games` games."""
    return [
        StatLine(pid, "season", len(games), 0.0, {k: fmean(g.stats[k] for g in games) for k in STAT_KEYS})
        for pid, games in logs.items()
        if len(games) >= min_games
    ]


def select_pool(lines: list[StatLine], categories: list[Category], size: int) -> list[StatLine]:
    """The top `size` lines by plain z-sum."""
    z = player_zscores(lines, categories)
    return sorted(lines, key=lambda s: -z[s.player_id].value)[:size]


def full_weeks(games: list[Game], min_week_games: int) -> list[list[Game]]:
    """His Monday-Sunday weeks with at least `min_week_games` games, in order."""
    by_week: dict[tuple[int, int], list[Game]] = defaultdict(list)
    for g in games:
        by_week[g.day.isocalendar()[:2]].append(g)
    return [gs for _, gs in sorted(by_week.items()) if len(gs) >= min_week_games]


def _average(games: list[Game]) -> dict[str, float]:
    return {k: fmean(g.stats[k] for g in games) for k in STAT_KEYS}


def fit_bounce(
    logs: dict[int, list[Game]],
    pool: list[StatLine],
    categories: list[Category],
    min_week_games: int = MIN_WEEK_GAMES,
    min_weeks: int = MIN_WEEKS,
) -> dict[str, CategoryBounce]:
    """Each category's weekly bounce over the pool; players with fewer than `min_weeks`
    full weeks are left out of it."""
    base = baselines(pool, categories)
    weekly: dict[str, list[float]] = defaultdict(list)
    single: dict[str, list[float]] = defaultdict(list)
    for line in pool:
        weeks = full_weeks(logs[line.player_id], min_week_games)
        if len(weeks) < max(min_weeks, 2):  # a variance needs two weeks
            continue
        games = [g for week in weeks for g in week]
        per_game_to_week = fmean(1 / len(week) for week in weeks)  # independent games: var / games
        for cat in categories:
            rate = base[cat.key].league_pct
            weekly[cat.key].append(variance(raw_value(_average(week), cat, rate) for week in weeks))
            single[cat.key].append(variance(raw_value(g.stats, cat, rate) for g in games) * per_game_to_week)
    return {
        cat.key: CategoryBounce(fmean(weekly[cat.key]), fmean(single[cat.key]), base[cat.key].mean, base[cat.key].sd)
        for cat in categories
        if weekly[cat.key]
    }


def _get_json(url: str, cache: Path | None, headers: dict[str, str] | None = None) -> dict:
    if cache is not None and cache.exists():
        return json.loads(cache.read_text())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT} | (headers or {}))
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data


def fetch_candidates(season: int, n: int, cache_dir: Path | None) -> list[int]:
    """The `n` most-owned players in ESPN fantasy for `season`, most owned first."""
    fa_filter = {"players": {"limit": n, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    cache = cache_dir / f"players_{season}_{n}.json" if cache_dir else None
    data = _get_json(PLAYERS_URL.format(season=season), cache, {"x-fantasy-filter": json.dumps(fa_filter)})
    return [int(p["player"]["id"]) for p in data["players"]]


def _fetch(url: str, cache: Path | None) -> dict:
    fetched = cache is None or not cache.exists()
    data = _get_json(url, cache)
    if fetched:
        time.sleep(REQUEST_GAP)
    return data


def fetch_raw_logs(season: int, player_ids: list[int], cache_dir: Path | None) -> dict[int, dict]:
    logs = {}
    for i, pid in enumerate(player_ids, 1):
        cache = cache_dir / f"gamelog_{season}_{pid}.json" if cache_dir else None
        try:
            logs[pid] = _fetch(GAMELOG_URL.format(player_id=pid, season=season), cache)
        except urllib.error.HTTPError as e:
            print(f"  player {pid}: HTTP {e.code}, skipped")
        if i % 50 == 0:
            print(f"  {i}/{len(player_ids)} game logs")
    return logs


def fetch_game_days(first: date, last: date, cache_dir: Path | None) -> dict[str, date]:
    """Event id -> US date for every NBA game from `first` to `last`, one scoreboard per day."""
    out = {}
    for n in range((last - first).days + 1):
        day = first + timedelta(days=n)
        cache = cache_dir / f"scoreboard_{day:%Y%m%d}.json" if cache_dir else None
        out |= parse_scoreboard(_fetch(SCOREBOARD_URL.format(day=day), cache), day)
    return out


def _weight(var: float, sd: float) -> float:
    return 1 / sqrt(1 + var / sd**2) if sd else 1.0


def report(fit: dict[str, CategoryBounce]) -> str:
    """A table of the fit, with the weights it gives against the fitted season's own spread."""
    raw = {k: _weight(c.weekly_var, c.pool_sd) for k, c in fit.items()}
    scale = fmean(raw.values())
    rows = [f"{'cat':>4} {'weekly var':>11} {'single-game':>11} {'clustering':>10} {'weight':>7} {'scaled':>7}"]
    for k, c in fit.items():
        ratio = c.weekly_var / c.single_game_var if c.single_game_var else float("nan")
        rows.append(
            f"{k:>4} {c.weekly_var:11.4f} {c.single_game_var:11.4f} {ratio:10.2f} {raw[k]:7.2f} {raw[k] / scale:7.2f}"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit the weekly bounce for G-score weights from a past season.")
    parser.add_argument("--season", type=int, default=get_settings().season - 1, help="ESPN season, e.g. 2026")
    parser.add_argument("--cache", type=Path, help="directory to keep raw ESPN responses in")
    parser.add_argument("--candidates", type=int, default=CANDIDATES)
    parser.add_argument("--pool", type=int, default=POOL_SIZE)
    parser.add_argument("--min-games", type=int, default=MIN_GAMES)
    parser.add_argument("--min-week-games", type=int, default=MIN_WEEK_GAMES)
    parser.add_argument("--min-weeks", type=int, default=MIN_WEEKS)
    args = parser.parse_args()

    categories = nine_categories()
    ids = fetch_candidates(args.season, args.candidates, args.cache)
    print(f"{len(ids)} candidates; fetching game logs")
    raw_logs = fetch_raw_logs(args.season, ids, args.cache)
    days = [d for raw in raw_logs.values() for d in log_days(raw)]
    print(f"Fetching scoreboards {min(days) - timedelta(days=1)} to {max(days)} for each game's US date")
    game_days = fetch_game_days(min(days) - timedelta(days=1), max(days), args.cache)
    logs = {pid: parse_gamelog(raw, game_days) for pid, raw in raw_logs.items()}
    pool = select_pool(season_lines(logs, args.min_games), categories, args.pool)
    fit = fit_bounce(logs, pool, categories, args.min_week_games, args.min_weeks)
    out = {
        "season": args.season,
        "source": f"ESPN NBA game logs ({GAMELOG_URL}), candidates from ESPN fantasy ownership ({PLAYERS_URL})",
        "fitted": date.today().isoformat(),
        "candidates": len(ids),
        "pool_size": len(pool),
        "min_games": args.min_games,
        "min_week_games": args.min_week_games,
        "min_weeks": args.min_weeks,
        "categories": {k: asdict(c) for k, c in fit.items()},
    }
    path = bounce_path(args.season)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(report(fit))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
