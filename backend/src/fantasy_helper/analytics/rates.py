"""Best-estimate per-game rates: ESPN's projection, shrunk toward actual season
averages as games accumulate.

w = gp / (gp + k), rate = w * season + (1 - w) * projected. With k = 10, a player
10 games in is half projection, half actual. Makes and attempts are blended
separately, so percentages still come out of the totals. k is a shrinkage
constant to be tuned by backtest.
"""

from fantasy_helper.domain import STAT_KEYS, StatLine

BLENDED = "blended"
DEFAULT_K = 10.0


def _check_window(lines: list[StatLine], window: str) -> dict[int, StatLine]:
    wrong = {s.window for s in lines} - {window}
    if wrong:
        raise ValueError(f"Expected {window} lines, got {sorted(wrong)}")
    return {s.player_id: s for s in lines}


def blend_rates(season_lines: list[StatLine], projected_lines: list[StatLine], k: float = DEFAULT_K) -> list[StatLine]:
    season = _check_window(season_lines, "season")
    projected = _check_window(projected_lines, "projected")
    out = []
    for player_id in sorted(season.keys() | projected.keys()):
        s, p = season.get(player_id), projected.get(player_id)
        gp = s.gp if s else 0.0
        if p is None:
            per_game, mpg = dict(s.per_game), s.mpg
        elif s is None:
            per_game, mpg = dict(p.per_game), p.mpg
        else:
            w = gp / (gp + k)
            per_game = {key: w * s.per_game[key] + (1 - w) * p.per_game[key] for key in STAT_KEYS}
            mpg = w * s.mpg + (1 - w) * p.mpg
        out.append(StatLine(player_id, BLENDED, gp, mpg, per_game))
    return out
