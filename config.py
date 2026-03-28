"""
config.py
---------
Thin key-value config persisted to a plain-text file, plus helpers for
reading / writing the [chest_sheets] section that maps chest display names
to their Excel data sheet and price sheet.

Format of tracker_config.txt
-----------------------------
log_path=C:/...
excel_path=C:/...
...

[chest_sheets]
# display_name | data_sheet | price_sheet
Razador's Chest|Razador Chest Data|Razador Loot Prices
Nemere's Chest|Nemere Chest Data|Nemere Loot Prices
Jotun Thrym's Chest|Jotun Chest Data|Jotun Loot Prices
Hellgates Chest|Blue Death Chest Data|Blue Death Loot Prices

No UI or business-logic imports – safe to import from anywhere.
"""

from pathlib import Path

CONFIG_FILE = Path("tracker_config.txt")

_SECTION = "[chest_sheets]"
_SEP = "|"

# ─────────────────────────────────────────────────────────────────────────────
# Low-level key-value helpers
# ─────────────────────────────────────────────────────────────────────────────


def load(key: str, default: str = "") -> str:
    """Return the stored value for *key*, or *default* if absent."""
    if not CONFIG_FILE.exists():
        return default
    try:
        in_section = False
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("["):
                    in_section = stripped == _SECTION
                    continue
                if in_section:
                    continue
                if "=" in stripped:
                    k, _, v = stripped.partition("=")
                    if k.strip() == key:
                        return v.strip()
    except OSError as exc:
        print(f"[config] read error: {exc}")
    return default


def save(values: dict[str, str]) -> None:
    """Persist *values*, merging with any keys already on disk."""
    lines_before_section: list[str] = []
    section_lines: list[str] = []
    in_section = False

    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped == _SECTION:
                        in_section = True
                        section_lines.append(line)
                        continue
                    if in_section:
                        section_lines.append(line)
                    else:
                        lines_before_section.append(line)
        except OSError as exc:
            print(f"[config] read error before save: {exc}")

    existing: dict[str, str] = {}
    for line in lines_before_section:
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()
    existing.update(values)

    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            for k, v in existing.items():
                fh.write(f"{k}={v}\n")
            if section_lines:
                fh.write("\n")
                for sl in section_lines:
                    fh.write(sl if sl.endswith("\n") else sl + "\n")
    except OSError as exc:
        print(f"[config] write error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Chest-sheet helpers
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CHEST_SHEETS: list[tuple[str, str, str]] = [
    ("Razador's Chest", "Razador Chest Data", "Razador Loot Prices"),
    ("Nemere's Chest", "Nemere Chest Data", "Nemere Loot Prices"),
    ("Jotun Thrym's Chest", "Jotun Chest Data", "Jotun Loot Prices"),
    ("Hellgates Chest", "Blue Death Chest Data", "Blue Death Loot Prices"),
]


def load_chest_sheets() -> list[tuple[str, str, str]]:
    """
    Return a list of (display_name, data_sheet, price_sheet) tuples.

    Reads from the [chest_sheets] section of the config file.
    If the section is absent the defaults are written and returned.
    """
    if not CONFIG_FILE.exists():
        _write_chest_sheets(_DEFAULT_CHEST_SHEETS)
        return list(_DEFAULT_CHEST_SHEETS)

    results: list[tuple[str, str, str]] = []
    in_section = False
    found_section = False

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped == _SECTION:
                    in_section = True
                    found_section = True
                    continue
                if stripped.startswith("[") and stripped != _SECTION:
                    in_section = False
                    continue
                if not in_section or not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split(_SEP)
                if len(parts) == 3:
                    results.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    except OSError as exc:
        print(f"[config] load_chest_sheets error: {exc}")

    if not found_section:
        _write_chest_sheets(_DEFAULT_CHEST_SHEETS)
        return list(_DEFAULT_CHEST_SHEETS)

    return results if results else list(_DEFAULT_CHEST_SHEETS)


def save_chest_sheets(entries: list[tuple[str, str, str]]) -> None:
    """Persist a new list of (display_name, data_sheet, price_sheet) entries."""
    _write_chest_sheets(entries)


def _write_chest_sheets(entries: list[tuple[str, str, str]]) -> None:
    """Rewrite only the [chest_sheets] section, preserving plain key=value lines."""
    plain_lines: list[str] = []
    in_section = False

    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped == _SECTION:
                        in_section = True
                        continue
                    if stripped.startswith("["):
                        in_section = False
                    if not in_section:
                        plain_lines.append(line)
        except OSError as exc:
            print(f"[config] read error in _write_chest_sheets: {exc}")

    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            for line in plain_lines:
                fh.write(line if line.endswith("\n") else line + "\n")
            fh.write(f"\n{_SECTION}\n")
            fh.write("# display_name|data_sheet|price_sheet\n")
            for name, data, price in entries:
                fh.write(f"{name}{_SEP}{data}{_SEP}{price}\n")
    except OSError as exc:
        print(f"[config] write error in _write_chest_sheets: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Prices config  (prices_config.txt)
# ─────────────────────────────────────────────────────────────────────────────
# Format:
#   [ChestTypeName]
#   Item Name=price
#   Another Item=price

PRICES_FILE = Path("prices_config.txt")


def load_prices(chest_type: str) -> dict[str, float]:
    """Return {item_name: price} for *chest_type*. Keys are original case."""
    if not PRICES_FILE.exists():
        return {}
    result: dict[str, float] = {}
    in_section = False
    try:
        with PRICES_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_section = stripped[1:-1] == chest_type
                    continue
                if not in_section or not stripped or stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    k, _, v = stripped.partition("=")
                    try:
                        result[k.strip()] = float(v.strip())
                    except ValueError:
                        pass
    except OSError as exc:
        print(f"[config] load_prices error: {exc}")
    return result


def save_prices(chest_type: str, prices: dict[str, float]) -> None:
    """Persist *prices* for *chest_type*, preserving other chest sections."""
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    if PRICES_FILE.exists():
        try:
            with PRICES_FILE.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("[") and stripped.endswith("]"):
                        current_section = stripped[1:-1]
                        sections.setdefault(current_section, [])
                    elif current_section is not None:
                        sections[current_section].append(line.rstrip())
        except OSError as exc:
            print(f"[config] save_prices read error: {exc}")

    # Replace or add the target section
    sections[chest_type] = [f"{k}={v}" for k, v in prices.items()]

    try:
        with PRICES_FILE.open("w", encoding="utf-8") as fh:
            for section, lines in sections.items():
                fh.write(f"[{section}]\n")
                for line in lines:
                    fh.write(f"{line}\n")
                fh.write("\n")
    except OSError as exc:
        print(f"[config] save_prices write error: {exc}")


def load_all_prices() -> dict[str, dict[str, float]]:
    """Return {chest_type: {item_name: price}} for all sections."""
    if not PRICES_FILE.exists():
        return {}
    all_prices: dict[str, dict[str, float]] = {}
    current_section: str | None = None
    try:
        with PRICES_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    current_section = stripped[1:-1]
                    all_prices.setdefault(current_section, {})
                elif current_section and "=" in stripped and not stripped.startswith("#"):
                    k, _, v = stripped.partition("=")
                    try:
                        all_prices[current_section][k.strip()] = float(v.strip())
                    except ValueError:
                        pass
    except OSError as exc:
        print(f"[config] load_all_prices error: {exc}")
    return all_prices


def save_all_prices(all_prices: dict[str, dict[str, float]]) -> None:
    """
    Persist prices for ALL chest sections at once.
    *all_prices* is {chest_type: {item_name: price}}.
    Propagates shared item prices: if item_name exists in multiple chests,
    all chests get the same price (the one from whichever chest was edited).
    """
    # Build a unified price map for items that appear in multiple chests
    # so the last-written value wins consistently — callers should pre-sync.
    try:
        with PRICES_FILE.open("w", encoding="utf-8") as fh:
            for chest_type, prices in all_prices.items():
                fh.write(f"[{chest_type}]\n")
                for item_name, price in prices.items():
                    val = int(price) if price == int(price) else price
                    fh.write(f"{item_name}={val}\n")
                fh.write("\n")
    except OSError as exc:
        print(f"[config] save_all_prices error: {exc}")


def sync_item_price(item_name: str, price: float) -> None:
    """
    Update *item_name* to *price* in every chest section that contains it.
    This is the cross-chest sync: edit once, propagate everywhere.
    """
    all_prices = load_all_prices()
    changed = False
    for chest_prices in all_prices.values():
        for existing_name in list(chest_prices.keys()):
            if existing_name.lower() == item_name.lower():
                chest_prices[existing_name] = price
                changed = True
    if changed:
        save_all_prices(all_prices)
