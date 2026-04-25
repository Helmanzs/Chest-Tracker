"""
prices_config.py
----------------
Manages item prices and pinned items, stored in prices_config.txt.

Only stores what the USER has configured:
- Item prices (per chest type section)
- Pinned item lists (per chest type)

Default items come from chest_definitions.py — this file only overrides prices.

Format of prices_config.txt
----------------------------
pinned_items_Razadors_Chest=Shard,Energy Fragment
pinned_items_Nemeres_Chest=Shard,Energy Fragment

[Razador's Chest]
Horn of Razador=18400000
Charred Pass=1700000
...

[Nemere's Chest]
Horn of Nemere=8700000
...
"""

from __future__ import annotations

from pathlib import Path

PRICES_FILE = Path("prices_config.txt")

_PINNED_PREFIX = "pinned_items_"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _pinned_key(chest_type: str) -> str:
    safe = chest_type.replace("'", "").replace(" ", "_")
    return f"{_PINNED_PREFIX}{safe}"


def _read_file() -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """
    Parse prices_config.txt.
    Returns (top_level_kv, {chest_type: {item: price}})
    Top-level kv are lines before any [Section] header (used for pinned items).
    """
    top_kv: dict[str, str] = {}
    sections: dict[str, dict[str, float]] = {}
    current: str | None = None

    if not PRICES_FILE.exists():
        return top_kv, sections

    try:
        with PRICES_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    current = stripped[1:-1]
                    sections.setdefault(current, {})
                elif current is None:
                    # Top-level key=value (pinned items etc.)
                    if "=" in stripped:
                        k, _, v = stripped.partition("=")
                        top_kv[k.strip()] = v.strip()
                else:
                    if "=" in stripped:
                        k, _, v = stripped.partition("=")
                        try:
                            sections[current][k.strip()] = float(v.strip())
                        except ValueError:
                            pass
    except OSError as exc:
        print(f"[prices_config] read error: {exc}")

    return top_kv, sections


def _write_file(top_kv: dict[str, str], sections: dict[str, dict[str, float]]) -> None:
    """Write the full prices_config.txt atomically."""
    try:
        with PRICES_FILE.open("w", encoding="utf-8") as fh:
            # Top-level keys first (pinned items)
            for k, v in top_kv.items():
                fh.write(f"{k}={v}\n")
            if top_kv:
                fh.write("\n")
            # Chest sections
            for chest_type, prices in sections.items():
                fh.write(f"[{chest_type}]\n")
                for item_name, price in prices.items():
                    val = int(price) if price == int(price) else price
                    fh.write(f"{item_name}={val}\n")
                fh.write("\n")
    except OSError as exc:
        print(f"[prices_config] write error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Prices API
# ─────────────────────────────────────────────────────────────────────────────


def load_prices(chest_type: str) -> dict[str, float]:
    """Return {item_name: price} for *chest_type*. Only user-set prices."""
    _, sections = _read_file()
    return dict(sections.get(chest_type, {}))


def load_all_prices() -> dict[str, dict[str, float]]:
    """Return {chest_type: {item_name: price}} for all sections."""
    _, sections = _read_file()
    return {ct: dict(p) for ct, p in sections.items()}


def save_prices(chest_type: str, prices: dict[str, float]) -> None:
    """Persist *prices* for *chest_type*, preserving other sections."""
    top_kv, sections = _read_file()
    sections[chest_type] = dict(prices)
    _write_file(top_kv, sections)


def save_all_prices(all_prices: dict[str, dict[str, float]]) -> None:
    """Persist prices for ALL chest sections at once."""
    top_kv, _ = _read_file()
    _write_file(top_kv, all_prices)


def sync_item_price(item_name: str, price: float) -> None:
    """
    Update *item_name* to *price* in every chest section that contains it.
    Cross-chest sync: edit once, propagate everywhere.
    """
    top_kv, sections = _read_file()
    changed = False
    for chest_prices in sections.values():
        for existing_name in list(chest_prices.keys()):
            if existing_name.lower() == item_name.lower():
                chest_prices[existing_name] = price
                changed = True
    if changed:
        _write_file(top_kv, sections)


# ─────────────────────────────────────────────────────────────────────────────
# Pinned items API
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_PINNED = ["Shard", "Energy Fragment", "Storm Crystal Shard"]


def load_pinned_items(chest_type: str) -> list[str]:
    """Return pinned item names for *chest_type*."""
    top_kv, _ = _read_file()
    key = _pinned_key(chest_type)
    if key in top_kv:
        val = top_kv[key]
        items = [x.strip() for x in val.split(",") if x.strip()]
        return items if items else list(_DEFAULT_PINNED)
    return list(_DEFAULT_PINNED)


def save_pinned_items(chest_type: str, items: list[str]) -> None:
    """Persist the pinned items list for *chest_type*."""
    top_kv, sections = _read_file()
    key = _pinned_key(chest_type)
    top_kv[key] = ",".join(items)
    _write_file(top_kv, sections)
