"""
constants.py
------------
Chest-type mappings loaded from chest_definitions.py (static, app-bundled).
Users do not configure chest types — they are updated with the app.
"""

from chest_definitions import CHEST_DEFINITIONS, PATTERN_CHEST_DEFINITIONS

# ---------------------------------------------------------------------------
# Build lookup dicts from static definitions
# ---------------------------------------------------------------------------

CHEST_DATA_SHEETS: dict[str, str] = {name: name for name, _, _ in CHEST_DEFINITIONS}
CHEST_DISPLAY_NAMES: dict[str, str] = {name: display for name, display, _ in CHEST_DEFINITIONS}
CHEST_COLORS: dict[str, str] = {name: color for name, _, color in CHEST_DEFINITIONS}

# ---------------------------------------------------------------------------
# Pattern chests: detected by loot signature, not log text
# ---------------------------------------------------------------------------

PATTERN_CHESTS: list[tuple[str, frozenset[str]]] = [
    (name, frozenset(i.lower() for i in items)) for name, items in PATTERN_CHEST_DEFINITIONS
]

# ---------------------------------------------------------------------------
# Other constants
# ---------------------------------------------------------------------------

IGNORED_ITEMS: set[str] = {"yang"}
NON_ITEM_COLUMNS: set[str] = {"#", "chest #", "chest", "date", "time", "timestamp"}
DEFAULT_CHEST_TYPE: str = next(iter(CHEST_DATA_SHEETS), "")
LOOT_TIMEOUT: float = 2.0
PRICE_TIER_HIGH: int = 700_000
PRICE_TIER_MID: int = 1_000
