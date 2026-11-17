"""Name lookup on real-style names; the synthetic league's "Team 4" and "Player 1000" can't test it."""

import pytest

from fantasy_helper.agent.resolve import PlayerName, TeamName, normalize, resolve_player, resolve_team

TEAMS = [
    TeamName(1, "Dennis", "Bicoastal Ballaz", "BCB"),
    TeamName(2, "Jordan K", "Splash Bros", "SPL"),
    TeamName(3, "Jordan M", "Dunk City", "DNK"),
    TeamName(4, "Priya", "Priya's Picks", "PP"),
    TeamName(5, "Team Five", "Team Five", "T5"),  # no named owner: the owner is the team name
]
PLAYERS = [
    PlayerName(1, "Nikola Jokić", "Dennis"),
    PlayerName(2, "De'Aaron Fox", "Priya"),
    PlayerName(3, "Jaren Jackson Jr.", "Jordan K"),
    PlayerName(4, "Jalen Williams", "free agent"),
    PlayerName(5, "Jalen Brunson", "Jordan M"),
    PlayerName(6, "Zion Williamson", "Priya"),
    PlayerName(7, "P.J. Washington", "free agent"),
    PlayerName(8, "Victor Wembanyama", "Dennis"),
    PlayerName(9, "Gary Trent Jr.", "free agent"),
    PlayerName(10, "Gary Payton II", "Jordan M"),
    PlayerName(11, "Shai Gilgeous-Alexander", "Jordan K"),
    PlayerName(12, "Kevin Durant", "Priya"),
    PlayerName(13, "Keon Durren", "free agent"),
    PlayerName(14, "Anthony Davis", "Dennis"),
    PlayerName(15, "Bradley Beal", "free agent"),
]


@pytest.mark.parametrize(
    ("name", "normal"),
    [
        ("Nikola Jokić", "nikola jokic"),
        ("De'Aaron Fox", "deaaron fox"),
        ("P.J. Washington", "pj washington"),
        ("Jaren Jackson Jr.", "jaren jackson"),
        ("Gary Payton II", "gary payton"),
        ("  KAT  ", "kat"),
    ],
)
def test_normalize_drops_accents_punctuation_and_suffixes(name, normal):
    assert normalize(name) == normal


@pytest.mark.parametrize("query", [None, "", "me", "My team", "mine"])
def test_no_team_or_me_is_my_team(query):
    assert resolve_team(query, TEAMS, my_team_id=1) == 1


@pytest.mark.parametrize(
    ("query", "team_id"),
    [
        ("Priya", 4),  # owner
        ("priya's picks", 4),  # team name
        ("SPL", 2),  # abbreviation
        ("3", 3),  # id
        ("Dunk City", 3),
        ("Jordan M", 3),
        ("Dennis's team", 1),  # owner, then more
        ("Bicoastal", 1),  # a word of the team name
        ("Prya", 4),  # a typo
        ("Team Five", 5),
    ],
)
def test_a_team_by_owner_name_abbreviation_or_id(query, team_id):
    assert resolve_team(query, TEAMS, my_team_id=1) == team_id


def test_two_owners_with_one_first_name_asks_which():
    with pytest.raises(ValueError, match="could be Jordan K .*Jordan M"):
        resolve_team("Jordan", TEAMS, my_team_id=1)


def test_an_unknown_team_lists_the_teams():
    with pytest.raises(ValueError, match="No team called 'Lakers'.*Teams: Dennis"):
        resolve_team("Lakers", TEAMS, my_team_id=1)


@pytest.mark.parametrize(
    ("query", "player_id"),
    [
        ("Nikola Jokic", 1),  # no accent
        ("jokic", 1),  # last name
        ("Deaaron Fox", 2),  # no apostrophe
        ("Jaren Jackson", 3),  # no suffix
        ("Jaren Jackson Jr", 3),
        ("PJ Washington", 7),
        ("Wemban", 8),  # the start of a word of it
        ("Victor Wembanyamma", 8),  # a typo
        ("Zion", 6),  # a unique first name
        ("Gary Trent", 9),  # two Garys: the full name picks one
        ("SGA", 11),  # initials, with the hyphen splitting the name
        ("AD", 14),  # initials win over the letters in "Bradley"
    ],
)
def test_a_player_by_name(query, player_id):
    assert resolve_player(query, PLAYERS) == player_id


@pytest.mark.parametrize(
    ("query", "names"),
    [("Jalen", "Jalen Williams .*Jalen Brunson"), ("Gary", "Gary Trent"), ("KD", "Kevin Durant .*Keon Durren")],
)
def test_a_shared_name_asks_which_and_says_where_each_plays(query, names):
    with pytest.raises(ValueError, match=f"could be {names}") as err:
        resolve_player(query, PLAYERS)
    assert "(free agent)" in str(err.value)


def test_williams_is_not_williamson():
    """A last name matches whole words, so "Williams" is only Jalen Williams."""
    assert resolve_player("Williams", PLAYERS) == 4


def test_an_unknown_player_suggests_close_names_and_says_the_pool_is_limited():
    with pytest.raises(ValueError, match="Did you mean Jalen Brunson") as err:
        resolve_player("Jalen Brunsen Smith", PLAYERS)
    assert "outside that pool" in str(err.value)
    with pytest.raises(ValueError, match="No player called 'LeBron James'"):
        resolve_player("LeBron James", PLAYERS)
    with pytest.raises(ValueError, match="name is needed"):
        resolve_player("  ", PLAYERS)
