"""
constants.py
------------
Chest-type mappings loaded dynamically from tracker_config.txt.

Format of [chest_sheets] section:
  chest_name|display_name|hex_color

Example:
  Razador's Chest|Razador|#c0392b
  My New Chest|New Chest|#8e44ad
"""

import config as _config

# ---------------------------------------------------------------------------
# Chest-type mappings  (loaded from config)
# ---------------------------------------------------------------------------


def _build_chest_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (CHEST_DATA_SHEETS, CHEST_DISPLAY_NAMES, CHEST_COLORS)."""
    data_sheets: dict[str, str] = {}  # chest_name -> chest_name (identity, kept for compat)
    display_names: dict[str, str] = {}  # chest_name -> display_name
    colors: dict[str, str] = {}  # chest_name -> hex_color
    for chest_name, display_name, hex_color in _config.load_chest_sheets():
        data_sheets[chest_name] = chest_name
        display_names[chest_name] = display_name
        colors[chest_name] = hex_color
    return data_sheets, display_names, colors


CHEST_DATA_SHEETS, CHEST_DISPLAY_NAMES, CHEST_COLORS = _build_chest_maps()

# ---------------------------------------------------------------------------
# Other constants
# ---------------------------------------------------------------------------

IGNORED_ITEMS: set[str] = {"yang"}
NON_ITEM_COLUMNS: set[str] = {"#", "chest #", "chest", "date", "time", "timestamp"}
DEFAULT_CHEST_TYPE: str = next(iter(CHEST_DATA_SHEETS), "")
LOOT_TIMEOUT: float = 2.0
PRICE_TIER_HIGH: int = 700_000
PRICE_TIER_MID: int = 1_000
