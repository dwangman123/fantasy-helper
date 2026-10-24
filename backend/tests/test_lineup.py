from fantasy_helper.analytics.lineup import assign_starters

# This league's slots (BE/IR included to check they're ignored).
SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1}
ANY = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT"]


def test_eleven_players_for_ten_slots_drops_the_lowest_priority():
    starters = assign_starters([(i, ANY) for i in range(11)], SLOTS)
    assert sorted(starters) == list(range(10))


def test_second_center_fills_utility():
    starters = assign_starters([(1, ["C", "UT"]), (2, ["C", "UT"])], SLOTS)
    assert sorted(starters.values()) == ["C", "UT"]


def test_placed_player_moves_so_a_later_player_can_start():
    # Player 1 (G/F) could take PG first; player 2 is PG-only, so 1 must shift.
    slots = {"PG": 1, "G": 1}
    starters = assign_starters([(1, ["PG", "G"]), (2, ["PG"])], slots)
    assert starters == {1: "G", 2: "PG"}


def test_player_without_an_open_eligible_slot_sits():
    starters = assign_starters([(1, ["C"]), (2, ["C"])], {"C": 1, "PG": 1})
    assert starters == {1: "C"}


def test_bench_and_ir_are_not_starting_slots():
    starters = assign_starters([(1, ["BE", "IR"])], SLOTS)
    assert starters == {}
