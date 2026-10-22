"""The league's scoring categories.

Categories are read from ESPN league settings (scoringItems) instead of being
hardcoded, so a league with different cats still works. Percentage cats carry
their makes/attempts components because a team's FG% is total makes over total
attempts, never an average of player percentages.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str  # e.g. "PTS", "FG%"
    espn_stat_id: int
    reverse: bool = False  # True when lower is better (TO)
    makes: str | None = None  # percentage cats only: component stat keys
    attempts: str | None = None

    @property
    def is_percentage(self) -> bool:
        return self.makes is not None


# ESPN basketball stat ids -> category definitions we know how to compute.
KNOWN_CATEGORIES: dict[int, Category] = {
    0: Category("PTS", 0),
    1: Category("BLK", 1),
    2: Category("STL", 2),
    3: Category("AST", 3),
    6: Category("REB", 6),
    11: Category("TO", 11, reverse=True),
    17: Category("3PM", 17),
    19: Category("FG%", 19, makes="FGM", attempts="FGA"),
    20: Category("FT%", 20, makes="FTM", attempts="FTA"),
}

# Display order used throughout the app (ESPN's own scoreboard order).
_DISPLAY_ORDER = ["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"]


def categories_from_scoring_items(items: list[dict]) -> list[Category]:
    """Build the category list from ESPN's settings.scoringSettings.scoringItems."""
    cats = []
    for item in items:
        stat_id = item["statId"]
        if stat_id not in KNOWN_CATEGORIES:
            raise ValueError(f"Unsupported scoring stat id {stat_id}; add it to KNOWN_CATEGORIES")
        cat = KNOWN_CATEGORIES[stat_id]
        if bool(item.get("isReverseItem")) != cat.reverse:
            raise ValueError(f"Reverse flag for {cat.key} differs from ESPN settings")
        cats.append(cat)
    order = {key: i for i, key in enumerate(_DISPLAY_ORDER)}
    return sorted(cats, key=lambda c: order.get(c.key, len(order)))
