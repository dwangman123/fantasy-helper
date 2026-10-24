"""Daily lineup: which players start, given the league's starting slots.

Only starters' games count, so on a busy day some games are lost. Candidates are
placed in priority order; each one tries for a slot through an augmenting path
(Kuhn's bipartite matching), which may shuffle already-placed players into other
eligible slots but never unseats them. That yields the most starters possible
while always preferring higher-priority players.
"""

NON_STARTING_SLOTS = {"BE", "IR"}


def assign_starters(candidates: list[tuple[int, list[str]]], slot_counts: dict[str, int]) -> dict[int, str]:
    """player_id -> starting slot. `candidates` is (player_id, eligible_slots), highest priority first."""
    seats = [name for name, n in slot_counts.items() if name not in NON_STARTING_SLOTS for _ in range(n)]
    eligible = {pid: set(slots) for pid, slots in candidates}
    holder: list[int | None] = [None] * len(seats)

    def place(pid: int, visited: set[int]) -> bool:
        for i, slot in enumerate(seats):
            if slot not in eligible[pid] or i in visited:
                continue
            visited.add(i)
            if holder[i] is None or place(holder[i], visited):
                holder[i] = pid
                return True
        return False

    for pid, _ in candidates:
        place(pid, set())
    return {pid: seats[i] for i, pid in enumerate(holder) if pid is not None}
