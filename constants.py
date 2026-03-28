"""
constants.py
------------
Static thresholds and derived chest-type mappings.

CHEST_DATA_SHEETS and CHEST_PRICE_SHEETS are now built dynamically from
tracker_config.txt so you can add new chest types without editing code:

  [chest_sheets]
  # display_name|data_sheet|price_sheet
  Razador's Chest|Razador Chest Data|Razador Loot Prices
  My New Chest|My New Chest Data|My New Loot Prices

Everything else remains a plain constant.
"""

import config as _config

# ---------------------------------------------------------------------------
# Chest-type mappings  (loaded from config, NOT hardcoded)
# ---------------------------------------------------------------------------


def _build_chest_maps() -> tuple[dict[str, str], dict[str, str]]:
    data_sheets: dict[str, str] = {}
    price_sheets: dict[str, str] = {}
    for display_name, data_sheet, price_sheet in _config.load_chest_sheets():
        data_sheets[display_name] = data_sheet
        price_sheets[display_name] = price_sheet
    return data_sheets, price_sheets


CHEST_DATA_SHEETS, CHEST_PRICE_SHEETS = _build_chest_maps()

# ---------------------------------------------------------------------------
# Other constants
# ---------------------------------------------------------------------------

# Items that appear in "You receive N X." lines but should never be tracked.
IGNORED_ITEMS: set[str] = {"yang"}

# Non-item column names in the Excel data sheet (used when summing revenue).
NON_ITEM_COLUMNS: set[str] = {"#", "chest #", "chest", "date", "time", "timestamp"}

# Default chest type shown on first run.
DEFAULT_CHEST_TYPE: str = next(iter(CHEST_DATA_SHEETS), "")

# Seconds of silence after the last loot line before the batch is considered complete.
LOOT_TIMEOUT: float = 2.0

# Price thresholds that control log text colouring.
PRICE_TIER_HIGH: int = 700_000
PRICE_TIER_MID: int = 1_000
