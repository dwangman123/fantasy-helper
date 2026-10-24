"""Per-game dispersion (variance / mean) per stat, fitted from logged games.

The matchup simulation draws game totals with variance = phi * mean. phi = 1 is
Poisson; real box scores are usually more spread out (minutes swing, blowouts,
foul trouble). phi is the pooled Pearson estimate over players with enough games:

    phi = sum_i sum_j (x_ij - mean_i)^2 / sum_i (n_i - 1) * mean_i

A stat is left out (so the simulation stays Poisson) until enough players have
enough games to trust the estimate, and the result is capped at MAX_PHI.
"""

from collections import defaultdict
from statistics import fmean

from fantasy_helper.domain import PlayerGameStat

MIN_GAMES_PER_PLAYER = 3
MIN_PLAYERS = 30
MAX_PHI = 5.0


def fit_dispersion(
    games: list[PlayerGameStat],
    keys: list[str],
    min_games: int = MIN_GAMES_PER_PLAYER,
    min_players: int = MIN_PLAYERS,
) -> dict[str, float]:
    by_player: dict[int, list[PlayerGameStat]] = defaultdict(list)
    for g in games:
        by_player[g.player_id].append(g)
    logs = [gs for gs in by_player.values() if len(gs) >= min_games]
    if len(logs) < min_players:
        return {}
    out = {}
    for key in keys:
        squares = expected = 0.0
        for gs in logs:
            values = [g.stats[key] for g in gs]
            mean = fmean(values)
            squares += sum((v - mean) ** 2 for v in values)
            expected += (len(values) - 1) * mean
        if expected > 0:
            out[key] = min(squares / expected, MAX_PHI)
    return out
