"""The agent's tools: the app's own analytics, called the way the pages call them.

Each tool wraps an API route function (`api/app.py`) with the same `Ctx`, so a tool's numbers
are the page's numbers, from the same per-snapshot cache. Results are trimmed to what a model
needs (top rows, no per-player z-score tables), name teams by their owner as the UI does, and
keep each report's assumptions and warnings so an answer can state them.

Numbers are formatted one way everywhere, so an answer quoting them can be checked against the
tool results (see agent/evals): probabilities and their changes in percent to 1 decimal
(`*_pct`, `*_pp`), categories per week to 2 decimals, G-score values to 1 decimal, per-game and
weekly stats to 1 decimal, FG% and FT% in percent to 1 decimal.

Argument schemas follow OpenAI's strict mode: every object closes its properties
(`additionalProperties: false`) and lists them all as required, with optional ones nullable.

The trade tools are `gated`: the Trades page sits behind a password (frontend/proxy.ts), so the
agent offers them only when its caller allows (`tools_for`).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fantasy_helper.agent.resolve import PlayerName, TeamName, normalize, resolve_player, resolve_team
from fantasy_helper.api import app as routes
from fantasy_helper.api import schemas as s
from fantasy_helper.api.app import WINDOWS, Ctx
from fantasy_helper.db import queries as q

TOP_ROWS = 8  # rows kept from each ranking
MOVES_SHOWN = 8
HISTORY_SHOWN = 8
MAX_NAMES = 5  # players per player_lookup call
MAX_RESULT_CHARS = 16_000  # a tool result's JSON, about 4,000 tokens
MIN_CHANGE_PP = 0.5  # per-category changes smaller than this are left out of a row

CATEGORY_WORDS = {
    "points": "PTS",
    "pts": "PTS",
    "rebounds": "REB",
    "boards": "REB",
    "reb": "REB",
    "assists": "AST",
    "ast": "AST",
    "steals": "STL",
    "stl": "STL",
    "blocks": "BLK",
    "blk": "BLK",
    "threes": "3PM",
    "3s": "3PM",
    "3pm": "3PM",
    "3 pointers": "3PM",
    "three pointers": "3PM",
    "turnovers": "TO",
    "to": "TO",
    "fg": "FG%",
    "fg%": "FG%",
    "field goal": "FG%",
    "field goal percentage": "FG%",
    "ft": "FT%",
    "ft%": "FT%",
    "free throw": "FT%",
    "free throw percentage": "FT%",
}


# --- Formatting ------------------------------------------------------------------------------


def pct(x: float) -> float:
    """A probability, or a change in one, in percent."""
    return round(100 * x, 1)


def cats(x: float) -> float:
    return round(x, 2)


def one(x: float) -> float:
    return round(x, 1)


def stat(key: str, x: float | None) -> float | None:
    """A stat value: FG% and FT% (fractions) in percent, the rest to 1 decimal."""
    if x is None:
        return None
    return pct(x) if key.endswith("%") else one(x)


def per_game(stats: dict[str, float] | None) -> dict[str, float] | None:
    """Counting stats plus FG% and FT% from makes and attempts."""
    if not stats:
        return None
    out = {k: one(stats[k]) for k in ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO") if k in stats}
    for pct_key, made, tried in (("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")):
        if stats.get(tried):
            out[pct_key] = pct(stats[made] / stats[tried])
    return out


def changes(by_cat: dict[str, float]) -> dict[str, float]:
    """Per-category changes in win chance worth mentioning, biggest first, in percentage points."""
    kept = {k: pct(v) for k, v in by_cat.items() if abs(pct(v)) >= MIN_CHANGE_PP}
    return dict(sorted(kept.items(), key=lambda kv: -abs(kv[1])))


class Names:
    """Team labels for one turn: the owner's name, as the UI shows it, marked for my team."""

    def __init__(self, c: Ctx):
        self.c = c
        self.teams = q.load_teams(c.session, c.season)
        self.my_team_id = c.settings.my_team_id
        self.labels = {t.team_id: t.owner + (" (you)" if t.team_id == self.my_team_id else "") for t in self.teams}

    def team(self, team_id: int | None) -> str | None:
        return None if team_id is None else self.labels.get(team_id, f"team {team_id}")

    def resolve(self, query: str | None) -> int:
        return resolve_team(
            query, [TeamName(t.team_id, t.owner, t.name, t.abbrev) for t in self.teams], self.my_team_id
        )

    def player_names(self, team_id: int | None = None) -> list[PlayerName]:
        """Rostered players and the stored free-agent pool, or one team's roster."""
        ctx = self.c.context()
        on_team = {r.player_id: r.team_id for r in ctx.rosters}
        return [
            PlayerName(pid, p.name, self.team(on_team[pid]) if pid in on_team else "free agent")
            for pid, p in ctx.players.items()
            if team_id is None or on_team.get(pid) == team_id
        ]


def player(p: s.PlayerOut | None) -> dict[str, Any] | None:
    if p is None:
        return None
    out: dict[str, Any] = {"name": p.name, "nba_team": p.pro_team, "position": p.position, "value": one(p.value)}
    if p.injury_status not in (None, "ACTIVE"):
        out["status"] = p.injury_status
        if p.out_for_season:
            out["out_for_season"] = True
        elif p.return_date:
            out["expected_return"] = p.return_date.isoformat()
        if p.injury_note:
            out["injury"] = p.injury_note
    return out


def category(names: list[str], query: str | None) -> str | None:
    if query is None:
        return None
    if query in names:
        return query
    key = CATEGORY_WORDS.get(normalize(query)) or CATEGORY_WORDS.get(query.strip().lower()) or query.strip().upper()
    if key not in names:
        raise ValueError(f"Unknown category {query!r}; one of {', '.join(names)}")
    return key


# --- Tools -----------------------------------------------------------------------------------


def _score(x: s.Score, fmt: Callable[[float], float]) -> dict[str, float]:
    return {"me": fmt(x.me), "opp": fmt(x.opp), "ties": fmt(x.ties)}


def get_matchup(c: Ctx, n: Names, team: str | None, week: int | None) -> dict[str, Any]:
    team_id = n.resolve(team)
    d = routes.matchup(c, team_id=team_id, period=week)
    out: dict[str, Any] = {
        "team": n.team(team_id),
        "opponent": n.team(d.opponent_id),
        "week": d.matchup_period,
        "final": d.final,
        "p_win_pct": pct(d.p_win),
        "p_tie_pct": pct(d.p_tie),
        "p_loss_pct": pct(d.p_loss),
    }
    if d.final:
        out["score"] = {"won": int(d.expected.me), "lost": int(d.expected.opp), "tied": int(d.expected.ties)}
    else:
        out |= {
            "days_left": len(d.days_left),
            "expected_categories": _score(d.expected, cats),
            "most_likely_score": _score(d.most_likely, int),
            "starts_left": {"me": one(d.starts_left_me), "opp": one(d.starts_left_opp)},
            "simulations": d.n_sims,
        }
    out["categories"] = [
        {
            "category": x.key,
            **({"lower_is_better": True} if x.reverse else {}),
            ("final" if d.final else "now"): {"me": stat(x.key, x.now_me), "opp": stat(x.key, x.now_opp)},
            **({} if d.final else {"projected": {"me": stat(x.key, x.proj_me), "opp": stat(x.key, x.proj_opp)}}),
            "p_win_pct": pct(x.p_win),
            **({"result": x.result} if x.result else {"label": x.label}),
        }
        for x in d.categories
    ]
    out["odds_history"] = [
        {"at": h.created_at.isoformat(timespec="minutes"), "p_win_pct": pct(h.p_win)}
        for h in d.history[-HISTORY_SHOWN:]
    ]
    out["notes"] = [
        "Projected totals are the week's expected finals: actual stats so far plus a simulation of the games left, "
        "with the best legal lineup each day. Labels: locked (> 90% to win), swing (10-90%), lost (< 10%).",
    ]
    return out


def get_team(c: Ctx, n: Names, team: str | None, window: str | None) -> dict[str, Any]:
    team_id = n.resolve(team)
    d = routes.team(c, team_id=team_id, window=window or "model")
    return {
        "team": n.team(team_id),
        "team_name": d.team.name,
        "record": f"{d.team.wins}-{d.team.losses}-{d.team.ties}",
        "window": d.window,
        "punts": d.punts,
        "categories": [
            {"category": x.key, "per_game": stat(x.key, x.value), "rank": x.rank, "z": one(x.z)} for x in d.profile
        ],
        "players": [
            {
                **player(x.player),
                "value": one(x.value),  # over this window, not the model's blend
                "slot": x.slot,
                "games": one(x.gp) if x.gp is not None else None,
                "per_game": per_game(x.per_game),
                "specialties": x.specialties,
                **({"acquired_by": x.acquisition_type} if x.acquisition_type in ("ADD", "TRADE") else {}),
            }
            for x in d.players
        ],
        "recent_moves": [
            {"date": x.at.date().isoformat() if x.at else None, "move": x.type, "player": x.player_name}
            for x in d.moves[:MOVES_SHOWN]
        ],
        "notes": d.notes[:3],
    }


def get_category_profiles(c: Ctx, n: Names, window: str | None) -> dict[str, Any]:
    d = routes.profiles(c, window=window or "model")
    return {
        "window": d.window,
        "teams": [
            {
                "team": n.team(t.team.team_id),
                "punts": t.punts,
                "rank": {x.key: x.rank for x in t.profile},
                "per_game": {x.key: stat(x.key, x.value) for x in t.profile},
            }
            for t in d.teams
        ],
        "notes": d.notes,
    }


def get_power_rankings(c: Ctx, n: Names) -> dict[str, Any]:
    d = routes.power(c)
    return {
        "weeks_covered": f"{d.first_period}-{d.first_period + d.weeks - 1}",
        "completed_weeks": d.completed_weeks,
        "rows": [
            {
                "rank": r.rank,
                "team": n.team(r.team.team_id),
                "strength_pct": pct(r.strength),
                "categories_per_week": cats(r.cats_per_week),
                "vs_everyone_so_far": f"{r.all_play.wins}-{r.all_play.losses}-{r.all_play.ties}",
                "projected_record": f"{one(r.projected.wins)}-{one(r.projected.losses)}-{one(r.projected.ties)}",
                "punts": r.punts,
            }
            for r in d.rows
        ],
        "notes": d.notes,
    }


def rank_waivers(
    c: Ctx, n: Names, team: str | None, pickups_play_today: bool | None, focus_category: str | None
) -> dict[str, Any]:
    team_id = n.resolve(team)
    d = routes.waivers(c, team_id=team_id, same_day=bool(pickups_play_today))
    focus = category([b.key for b in d.board], focus_category)
    stream, hold = list(d.stream), list(d.hold.rows)
    if focus:
        stream.sort(key=lambda o: -o.cat_d_p_win.get(focus, 0.0))
        hold.sort(key=lambda o: -o.cat_d_p_win.get(focus, 0.0))
    out = {
        "team": n.team(team_id),
        "opponent": n.team(d.opponent_id),
        "week": d.matchup_period,
        "pickups_first_play": d.first_day.isoformat(),
        "days_left": len(d.days_left),
        "now": {"p_win_pct": pct(d.baseline.p_win), "expected_categories": cats(d.baseline.expected_cats)},
        "board": [{"category": b.key, "label": b.label, "p_win_pct": pct(b.p_win)} for b in d.board],
        "stream_this_week": [
            {
                "add": player(o.add),
                "drop": player(o.drop),
                "games": o.games,
                "extra_starts": one(o.extra_starts),
                "p_win_pct": pct(o.p_win),
                "change_pp": pct(o.d_p_win),
                "change_expected_categories": cats(o.d_expected_cats),
                "category_change_pp": changes(o.cat_d_p_win),
                "flips_to_likely_win": o.gained,
                "flips_to_likely_loss": o.lost,
            }
            for o in stream[:TOP_ROWS]
        ],
        "hold_rest_of_season": {
            "weeks": d.hold.weeks,
            "first_week": d.hold.first_period,
            "categories_per_week_now": cats(d.hold.cats_per_week),
            "rows": [
                {
                    "add": player(o.add),
                    "drop": player(o.drop),
                    "games": o.games,
                    "change_categories_per_week": cats(o.d_cats_per_week),
                    "category_change_pp": changes(o.cat_d_p_win),
                }
                for o in hold[:TOP_ROWS]
            ],
        },
        "drops_considered": [player(p)["name"] if p else "an open roster spot" for p in d.drops],
        "evaluated": {"stream": d.stream_screened, "hold": d.hold.screened, "free_agents": d.free_agents},
        "assumptions": d.assumptions,
    }
    if focus:
        out["sorted_by"] = f"change in P(win {focus}), among the pickups evaluated in full"
    return out


def player_lookup(c: Ctx, n: Names, names: list[str]) -> dict[str, Any]:
    if not names or len(names) > MAX_NAMES:
        raise ValueError(f"Look up 1 to {MAX_NAMES} players at a time")
    ctx = c.context()
    on_team = {r.player_id: r for r in ctx.rosters}
    pool = n.player_names()
    values = sorted((z.value for z in ctx.z.values()), reverse=True)
    found = []
    for name in names:
        try:
            pid = resolve_player(name, pool)
        except ValueError as exc:
            found.append({"query": name, "error": str(exc)})
            continue
        p = routes._player(ctx, pid)
        entry = on_team.get(pid)
        games = ctx.schedule.get(ctx.players[pid].pro_team, {})
        found.append(
            {
                **player(p),
                "fantasy_team": n.team(entry.team_id) if entry else "free agent",
                **({"slot": entry.lineup_slot} if entry else {}),
                "value_rank": f"{1 + sum(v > p.value for v in values)} of {len(values)}",
                "z": {k: one(v) for k, v in p.z.items()},
                "per_game": per_game(ctx.rates.get(pid)),
                "games_left_this_week": sum(1 for d in ctx.days_left if d in games),
            }
        )
    return {
        "players": found,
        "notes": [
            "value is a G-score: the player's z-scores against rostered players, each weighted by how much its "
            "category bounces week to week, summed. value_rank is among rostered players and the stored free-agent "
            "pool. per_game is the blend of season averages and ESPN's projection that the simulations use.",
        ],
    }


def _trade_side(n: Names, side: s.TradeSideOut) -> dict[str, Any]:
    names = lambda ps: [p.name for p in ps]  # noqa: E731
    return {
        "team": n.team(side.team_id),
        "gets": [player(p) for p in side.receives],
        "sends": [player(p) for p in side.sends],
        **({"drops_to_make_room": names(side.drops)} if side.drops else {}),
        **({"open_spots_filled_from_free_agency": side.opened} if side.opened else {}),
        **({"counted_at_replacement_level": names(side.below_replacement)} if side.below_replacement else {}),
        "categories_per_week_now": cats(side.cats_per_week),
        "change_categories_per_week": cats(side.d_cats_per_week),
        "change_p_win_week_pp": pct(side.d_p_week),
        "category_change_pp": changes(side.cat_d_p_win),
        "value_balance": one(side.d_value),
    }


def evaluate_trade(c: Ctx, n: Names, partner: str, give: list[str], get: list[str], team: str | None) -> dict[str, Any]:
    team_id, partner_id = n.resolve(team), n.resolve(partner)
    give_ids = [resolve_player(x, n.player_names(team_id)) for x in give]
    get_ids = [resolve_player(x, n.player_names(partner_id)) for x in get]
    d = routes.trade(c, partner_id=partner_id, give=give_ids, get=get_ids, team_id=team_id)
    return {
        "me": _trade_side(n, d.me),
        "partner": _trade_side(n, d.partner),
        "lopsided": d.lopsided,
        "weeks": f"{d.first_period}-{d.first_period + d.weeks - 1}",
        "warnings": d.warnings,
        "assumptions": d.assumptions,
    }


def find_trades(c: Ctx, n: Names, team: str | None) -> dict[str, Any]:
    team_id = n.resolve(team)
    d = routes.trades(c, team_id=team_id)
    return {
        "team": n.team(team_id),
        "weeks": f"{d.first_period}-{d.first_period + d.weeks - 1}",
        "deals_found": len(d.deals),
        "deals": [
            {
                "partner": n.team(x.partner.team_id),
                "get": [p.name for p in x.me.receives],
                "give": [p.name for p in x.me.sends],
                "my_change_categories_per_week": cats(x.me.d_cats_per_week),
                "partner_change_categories_per_week": cats(x.partner.d_cats_per_week),
                "partner_value_balance": one(x.partner.d_value),
                "lopsided": x.lopsided,
            }
            for x in d.deals[:TOP_ROWS]
        ],
        "searched": {"deals": d.candidates, "scored_in_full": d.evaluated},
        "assumptions": d.assumptions,
    }


# --- Registry --------------------------------------------------------------------------------


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _maybe(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    """A nullable argument: strict mode lists every property as required, so optional means null."""
    out: dict[str, Any] = {"type": [kind, "null"], "description": description, **extra}
    if "enum" in extra:
        out["enum"] = [*extra["enum"], None]
    return out


TEAM = _maybe("string", "A team: its owner's first name, its team name, or null for my team.")
WINDOW = _maybe(
    "string",
    "Stat window: model (the blend the predictions use; the default), season, last_7, last_15, last_30 or projected.",
    enum=WINDOWS,
)
PLAYERS = {"type": "array", "items": {"type": "string"}}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., dict[str, Any]]  # (ctx, names, **arguments)
    gated: bool = False  # offered only when the caller allows (the trade tools)


TOOLS = [
    Tool(
        "get_matchup",
        "A team's head-to-head matchup: this week's win odds, expected and most likely category score, and each "
        "category's totals so far, projected finals and win chance; or a finished week's result.",
        _schema({"team": TEAM, "week": _maybe("integer", "A finished matchup week, or null for this week.")}),
        get_matchup,
    ),
    Tool(
        "get_team",
        "A team's roster (each player's per-game stats, G-score value, injury and specialties), its per-game "
        "category totals with league ranks and punts, and its recent adds, drops and trades.",
        _schema({"team": TEAM, "window": WINDOW}),
        get_team,
    ),
    Tool(
        "get_category_profiles",
        "Every team's per-game value and league rank in each category, and the categories each team punts.",
        _schema({"window": WINDOW}),
        get_category_profiles,
    ),
    Tool(
        "get_power_rankings",
        "Power rankings by rest-of-season strength, with each team's record against everyone so far and its "
        "projected record.",
        _schema({}),
        get_power_rankings,
    ),
    Tool(
        "rank_waivers",
        "Free agents ranked for a team two ways: streaming this week (change in the chance to win the current "
        "matchup) and holding for the rest of the season (change in categories won per week). Each row names the "
        "player to drop. focus_category re-sorts both lists by the change in one category's win chance.",
        _schema(
            {
                "team": TEAM,
                "pickups_play_today": _maybe(
                    "boolean", "Rank as if a pickup can play today rather than from tomorrow (unverified rule)."
                ),
                "focus_category": _maybe("string", "A category to sort by, e.g. STL, or null."),
            }
        ),
        rank_waivers,
    ),
    Tool(
        "player_lookup",
        "Up to 5 players by name: fantasy team or free agent, injury, G-score value and rank, z-score per "
        "category, per-game stats and games left this week.",
        _schema({"names": PLAYERS | {"description": "Player names, 1 to 5."}}),
        player_lookup,
    ),
    Tool(
        "evaluate_trade",
        "Score a trade for both teams over the rest of the season: change in categories won per week and in the "
        "chance to win a week, per-category changes, roster moves each side must make, and the partner's value "
        "balance. Up to 3 players a side.",
        _schema(
            {
                "partner": {"type": "string", "description": "The other team: its owner's first name or team name."},
                "give": PLAYERS | {"description": "Players the team sends."},
                "get": PLAYERS | {"description": "Players the partner sends."},
                "team": TEAM,
            }
        ),
        evaluate_trade,
        gated=True,
    ),
    Tool(
        "find_trades",
        "Search every 1-for-1 and 2-for-1 deal with every other team for ones that help a team without hurting the "
        "partner, best first.",
        _schema({"team": TEAM}),
        find_trades,
        gated=True,
    ),
]
BY_NAME = {t.name: t for t in TOOLS}


def tools_for(include_gated: bool = False) -> list[Tool]:
    return [t for t in TOOLS if include_gated or not t.gated]


def run_tool(c: Ctx, name: str, arguments: dict[str, Any], allowed: list[Tool] | None = None) -> dict[str, Any]:
    """A tool's result, or {"error": ...} for a bad name or argument, which the model can act on."""
    offered = {t.name: t for t in (allowed if allowed is not None else tools_for())}
    tool = offered.get(name)
    if tool is None:
        return {"error": f"No tool {name!r}; available: {', '.join(offered)}"}
    unknown = set(arguments) - set(tool.parameters["properties"])
    if unknown:
        return {"error": f"Unknown arguments for {name}: {', '.join(sorted(unknown))}"}
    args = {k: arguments.get(k) for k in tool.parameters["properties"]}
    try:
        return tool.run(c, Names(c), **args)
    except (KeyError, IndexError):
        raise  # a bug, not a bad argument: the caller logs it rather than the model retrying
    except (ValueError, LookupError) as exc:
        return {"error": str(exc)}


def result_text(result: dict[str, Any]) -> str:
    """A result as the model sees it."""
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)
