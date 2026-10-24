"""Opponent adjustment: how much more or less of each stat players get against a
given NBA team, as a multiplier on their per-game rate for that game.

    factor = team's allowed per game / league average allowed

shrunk toward 1.0 by the team's games played (w = gp / (gp + k)) and clamped to
[MIN_FACTOR, MAX_FACTOR]. Early in the season one game says little about a
defense, so factors start near 1.0 by design. Pace is included implicitly: a
fast team allows more of everything. Makes and attempts get separate factors,
so a team that allows a high FG% raises makes relative to attempts. k and the
clamp are to be tuned by backtest.
"""

from statistics import fmean

from fantasy_helper.domain import STAT_KEYS, OpponentStats

DEFAULT_K = 10.0
MIN_FACTOR, MAX_FACTOR = 0.85, 1.15


def opponent_factors(stats: list[OpponentStats], k: float = DEFAULT_K) -> dict[str, dict[str, float]]:
    """NBA team -> stat key -> multiplier for players facing that team."""
    if not stats:
        return {}
    league = {key: fmean(s.per_game[key] for s in stats) for key in STAT_KEYS}
    out = {}
    for s in stats:
        w = s.gp / (s.gp + k)
        out[s.pro_team] = {
            key: min(max(1.0 + w * (s.per_game[key] / league[key] - 1.0), MIN_FACTOR), MAX_FACTOR)
            if league[key]
            else 1.0
            for key in STAT_KEYS
        }
    return out
