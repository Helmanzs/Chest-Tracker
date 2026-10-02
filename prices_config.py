"""
User-configured item prices and pinned items, stored in prices_config.txt.

Default item lists come from chest_definitions.py; this file only stores
what the user changed.

    pinned_items_Razadors_Chest=Shard,Energy Fragment

    [Razador's Chest]
    Horn of Razador=18400000
"""

from __future__ import annotations

from pathlib import Path

PRICES_FILE = Path("prices_config.txt")
_PINNED_PREFIX = "pinned_items_"
_DEFAULT_PINNED = ["Shard", "Energy Fragment", "Storm Crystal Shard"]

TopLevel = dict[str, str]
Sections = dict[str, dict[str, float]]


def _pinned_key(chest_type: str) -> str:
    return _PINNED_PREFIX + chest_type.replace("'", "").replace(" ", "_")


def _read_file() -> tuple[TopLevel, Sections]:
    """Return (top-level key/values, {chest_type: {item: price}})."""
    top: TopLevel = {}
    sections: Sections = {}
    current: str | None = None

    if not PRICES_FILE.exists():
        return top, sections

    try:
        with PRICES_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1]
                    sections.setdefault(current, {})
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if current is None:
                    top[key] = value
                else:
                    try:
                        sections[current][key] = float(value)
                    except ValueError:
                        pass
    except OSError as exc:
        print(f"[prices_config] read error: {exc}")

    return top, sections


def _write_file(top: TopLevel, sections: Sections) -> None:
    try:
        with PRICES_FILE.open("w", encoding="utf-8") as fh:
            for key, value in top.items():
                fh.write(f"{key}={value}\n")
            if top:
                fh.write("\n")
            for chest_type, prices in sections.items():
                fh.write(f"[{chest_type}]\n")
                for item, price in prices.items():
                    fh.write(f"{item}={int(price) if price == int(price) else price}\n")
                fh.write("\n")
    except OSError as exc:
        print(f"[prices_config] write error: {exc}")


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def load_prices(chest_type: str) -> dict[str, float]:
    _, sections = _read_file()
    return dict(sections.get(chest_type, {}))


def load_all_prices() -> dict[str, dict[str, float]]:
    _, sections = _read_file()
    return {ct: dict(prices) for ct, prices in sections.items()}


def save_prices(chest_type: str, prices: dict[str, float]) -> None:
    top, sections = _read_file()
    sections[chest_type] = dict(prices)
    _write_file(top, sections)


def save_all_prices(all_prices: dict[str, dict[str, float]]) -> None:
    top, _ = _read_file()
    _write_file(top, all_prices)


def sync_item_price(item_name: str, price: float) -> None:
    """Set *item_name* to *price* in every chest section that contains it."""
    top, sections = _read_file()
    lower = item_name.lower()
    changed = False
    for prices in sections.values():
        for name in prices:
            if name.lower() == lower:
                prices[name] = price
                changed = True
    if changed:
        _write_file(top, sections)


# ---------------------------------------------------------------------------
# Pinned items
# ---------------------------------------------------------------------------


def load_pinned_items(chest_type: str) -> list[str]:
    top, _ = _read_file()
    raw = top.get(_pinned_key(chest_type))
    if raw is None:
        return list(_DEFAULT_PINNED)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or list(_DEFAULT_PINNED)


def save_pinned_items(chest_type: str, items: list[str]) -> None:
    top, sections = _read_file()
    top[_pinned_key(chest_type)] = ",".join(items)
    _write_file(top, sections)
