"""Lookup tables and constants derived from chest_definitions.py."""

import re

from chest_definitions import BOUNTY_TIER_GROUPS, CHEST_DEFINITIONS, DEFAULT_ITEMS, PATTERN_CHEST_DEFINITIONS

_NON_ALNUM = re.compile(r"[\W_]+")


def match_key(text: str) -> str:
    """
    Lower-case *text* keeping only letters and digits. The game log is
    inconsistent about apostrophes ("Monkey God’s", "Monkey God's",
    "Monkey God s", "Monkey Gods"), so names are compared by this key.
    """
    return _NON_ALNUM.sub("", text).lower()


CHEST_DATA_SHEETS: dict[str, str] = {name: name for name, _, _ in CHEST_DEFINITIONS}
CHEST_DISPLAY_NAMES: dict[str, str] = {name: display for name, display, _ in CHEST_DEFINITIONS}
CHEST_COLORS: dict[str, str] = {name: color for name, _, color in CHEST_DEFINITIONS}

# Chests detected by loot signature rather than by log text.
PATTERN_CHESTS: list[tuple[str, frozenset[str]]] = [
    (name, frozenset(match_key(i) for i in items)) for name, items in PATTERN_CHEST_DEFINITIONS
]

# match_key -> canonical item name, so loot logged as "Nataraja s Medallion"
# is stored (and priced) as "Nataraja's Medallion".
CANONICAL_ITEMS: dict[str, str] = {}
for _items in DEFAULT_ITEMS.values():
    for _name in _items:
        CANONICAL_ITEMS.setdefault(match_key(_name), _name)


def canonical_item_name(name: str) -> str:
    return CANONICAL_ITEMS.get(match_key(name), name)


IGNORED_ITEMS: set[str] = {"yang"}
DEFAULT_CHEST_TYPE: str = next(iter(CHEST_DATA_SHEETS), "")
LOOT_TIMEOUT: float = 2.0
PRICE_TIER_HIGH: int = 700_000
PRICE_TIER_MID: int = 1_000


def bounty_group_key(chest_name: str) -> str | None:
    """Return the BOUNTY_TIER_GROUPS key that is, or contains, *chest_name*."""
    if chest_name in BOUNTY_TIER_GROUPS:
        return chest_name
    for key, tiers in BOUNTY_TIER_GROUPS.items():
        if chest_name in tiers:
            return key
    return None
