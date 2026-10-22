"""Minimal stand-ins for espn_api objects, shaped like the real ones."""

from types import SimpleNamespace


def fake_player(player_id=1, name="Test Player", pro_team="BOS", stats=None, schedule=None, **kw):
    return SimpleNamespace(
        playerId=player_id,
        name=name,
        proTeam=pro_team,
        position=kw.get("position", "SF"),
        eligibleSlots=kw.get("eligibleSlots", ["SF", "F", "UT", "BE", "IR"]),
        injuryStatus=kw.get("injuryStatus", "ACTIVE"),
        lineupSlot=kw.get("lineupSlot", "SF"),
        acquisitionType=kw.get("acquisitionType", "DRAFT"),
        stats=stats or {},
        schedule=schedule or {},
    )


def stat_block(gp=10, **per_game):
    avg = {"GP": gp, "MPG": 30.0, **per_game}
    return {"avg": avg, "total": {"GP": gp}, "applied_total": 0, "applied_avg": 0}
