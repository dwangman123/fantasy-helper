"""Names the model writes, turned into the ids the services take.

A team can be named by its owner (what the UI calls it), its team name, its abbreviation, its
id, or "me". A player by his name or initials ("SGA"), forgiving accents, punctuation, suffixes and small
typos.
A name that matches nothing, or more than one, raises ValueError with the candidates, which the
agent loop hands back to the model so it can pick one or ask.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches

ME = {"me", "my team", "mine", "my", "myself", "i", "you", "your team"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# How alike a misspelled name must be to its best match to be taken without asking.
TYPO_CUTOFF = 0.85
SUGGEST_CUTOFF = 0.6
MAX_SUGGESTIONS = 5


def normalize(name: str) -> str:
    """Lowercase ASCII words: "Nikola Jokić" -> "nikola jokic", "De'Aaron Fox" -> "deaaron fox",
    "P.J. Washington Jr." -> "pj washington"."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"['’.]", "", text)  # De'Aaron, P.J.
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    while len(words) > 1 and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


@dataclass(frozen=True)
class TeamName:
    team_id: int
    owner: str
    name: str
    abbrev: str


def resolve_team(query: str | None, teams: list[TeamName], my_team_id: int) -> int:
    """`query` None or "me" is my team."""
    if query is None or normalize(query) in ME | {""}:
        return my_team_id
    q = normalize(query)
    if q.isdigit() and int(q) in {t.team_id for t in teams}:
        return int(q)
    names = {t.team_id: [n for n in (normalize(t.owner), normalize(t.name), normalize(t.abbrev)) if n] for t in teams}
    # "Dennis's team" -> "dennis"; tried after the query as written, so "Team Five" still matches.
    bare = normalize(re.sub(r"['’]s\b", "", query)).removesuffix(" team").strip()
    for text in dict.fromkeys(t for t in (q, bare) if t):  # never empty: every name starts with ''
        for match in (
            lambda n, t=text: n == t,
            lambda n, t=text: t in n.split(),  # a word of it: "Bicoastal" for "Bicoastal Ballaz"
            lambda n, t=text: n.startswith(t),  # "Denn"
        ):
            found = sorted({tid for tid, ns in names.items() if any(match(n) for n in ns)})
            if len(found) == 1:
                return found[0]
            if len(found) > 1:
                raise ValueError(f"{query!r} could be {_teams(teams, found)}; which one?")
    close = _closest(q, {n: tid for tid, ns in names.items() for n in ns})
    if len(close) == 1 and _ratio(q, close[0][0]) >= TYPO_CUTOFF:
        return close[0][1]
    hint = f" Closest: {_teams(teams, sorted({tid for _, tid in close}))}." if close else ""
    raise ValueError(f"No team called {query!r}.{hint} Teams: {_teams(teams, [t.team_id for t in teams])}.")


@dataclass(frozen=True)
class PlayerName:
    player_id: int
    name: str
    where: str  # e.g. "Dennis's team" or "free agent", to tell two players apart


def resolve_player(query: str, players: list[PlayerName]) -> int:
    """Exact names first, then a unique last or first name, initials, the start or a run of a name,
    then a unique close spelling."""
    q = normalize(query)
    if not q:
        raise ValueError("A player's name is needed")
    by_name: dict[str, list[PlayerName]] = {}
    for p in players:
        by_name.setdefault(normalize(p.name), []).append(p)
    if q in by_name:
        return _one(query, by_name[q])
    words = q.split()
    if len(words) == 1:  # "Jokic", or a first name
        found = [p for n, ps in by_name.items() for p in ps if words[0] in n.split()]
        if found:
            return _one(query, found)
    if 2 <= len(q) <= 4 and q.isalpha():  # initials ("SGA"), before a run of letters ("AD" in "Bradley")
        found = [p for n, ps in by_name.items() for p in ps if "".join(w[0] for w in n.split()) == q]
        if found:
            return _one(query, found)
    # The start of a name or of one of its words ("Wemban"), or a run of it.
    found = [p for n, ps in by_name.items() for p in ps if q in n or any(w.startswith(q) for w in n.split())]
    if found:
        return _one(query, found)
    close = _closest(q, {n: n for n in by_name})
    if len(close) == 1 and _ratio(q, close[0][0]) >= TYPO_CUTOFF:
        return _one(query, by_name[close[0][0]])
    suggestions = [p for n, _ in close for p in by_name[n]]
    hint = f" Did you mean {_players(suggestions)}?" if suggestions else ""
    raise ValueError(
        f"No player called {query!r} among rostered players and the stored free-agent pool.{hint} A player outside "
        "that pool can't be looked up."
    )


def _one(query: str, found: list[PlayerName]) -> int:
    if len(found) == 1:
        return found[0].player_id
    raise ValueError(f"{query!r} could be {_players(found[:MAX_SUGGESTIONS])}; which one?")


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _closest(q: str, names: dict[str, object]) -> list[tuple[str, object]]:
    return [(n, names[n]) for n in get_close_matches(q, list(names), n=MAX_SUGGESTIONS, cutoff=SUGGEST_CUTOFF)]


def _teams(teams: list[TeamName], ids: list[int]) -> str:
    by_id = {t.team_id: t for t in teams}
    return ", ".join(
        f"{by_id[i].owner} ({by_id[i].name})" if by_id[i].owner != by_id[i].name else by_id[i].name for i in ids
    )


def _players(players: list[PlayerName]) -> str:
    return ", ".join(f"{p.name} ({p.where})" for p in players)
