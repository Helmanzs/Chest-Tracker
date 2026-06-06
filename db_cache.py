"""
db_cache.py
-----------
TTL-based in-memory cache for expensive Supabase read queries.

Cache keys are scoped per chest_type. Writes to the DB call
invalidate() so the next read re-fetches fresh data.

All public functions are thread-safe via a single RLock.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any

# How long cached data is considered fresh (seconds).
# Raise this if you want less network traffic; lower it for fresher data.
CACHE_TTL = 120.0  # 2 minutes

_lock = threading.RLock()


@dataclass
class _Entry:
    value: Any
    expires_at: float


# Separate stores so we can invalidate individual categories.
_drop_rates: dict[str, _Entry] = {}
_avg_quantities: dict[str, _Entry] = {}
_statistics: dict[str, _Entry] = {}
_loot_rows: dict[str, _Entry] = {}

# Monotonically-incrementing generation counter per chest_type.
# Bumped on every write; readers that hold a generation snapshot can
# detect staleness without waiting for TTL expiry.
_generation: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.monotonic()


def _get(store: dict, key: str) -> Any | None:
    with _lock:
        entry = store.get(key)
        if entry is None or _now() > entry.expires_at:
            return None
        return entry.value


def _set(store: dict, key: str, value: Any) -> None:
    with _lock:
        store[key] = _Entry(value=value, expires_at=_now() + CACHE_TTL)


# ---------------------------------------------------------------------------
# Public get/set helpers
# ---------------------------------------------------------------------------


def get_drop_rates(chest_type: str) -> dict[str, float] | None:
    return _get(_drop_rates, chest_type)


def set_drop_rates(chest_type: str, value: dict[str, float]) -> None:
    _set(_drop_rates, chest_type, value)


def get_avg_quantities(chest_type: str) -> dict[str, float] | None:
    return _get(_avg_quantities, chest_type)


def set_avg_quantities(chest_type: str, value: dict[str, float]) -> None:
    _set(_avg_quantities, chest_type, value)


def get_statistics(chest_type: str):  # -> Stats | None
    return _get(_statistics, chest_type)


def set_statistics(chest_type: str, value) -> None:
    _set(_statistics, chest_type, value)


def get_loot_rows(chest_type: str) -> list[dict] | None:
    return _get(_loot_rows, chest_type)


def set_loot_rows(chest_type: str, value: list[dict]) -> None:
    _set(_loot_rows, chest_type, value)


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def invalidate(chest_type: str) -> None:
    """
    Expire all cached data for *chest_type* immediately.
    Call after any write to that chest type.
    """
    with _lock:
        for store in (_drop_rates, _avg_quantities, _statistics, _loot_rows):
            store.pop(chest_type, None)
        _generation[chest_type] = _generation.get(chest_type, 0) + 1


def invalidate_all() -> None:
    """Wipe the entire cache (e.g. after reconnect)."""
    with _lock:
        for store in (_drop_rates, _avg_quantities, _statistics, _loot_rows):
            store.clear()
        _generation.clear()


def generation(chest_type: str) -> int:
    with _lock:
        return _generation.get(chest_type, 0)


# ---------------------------------------------------------------------------
# Statistics patch-through (avoids a full re-fetch after a single write)
# ---------------------------------------------------------------------------


def patch_statistics_after_write(
    chest_type: str,
    chest_revenue: float,
    loot: list[tuple[int, str]],
    item_prices: dict[str, float],
) -> None:
    """
    Update the cached Stats object in-place when a new chest is written,
    so the viewer can show updated numbers without a round-trip.
    Does nothing if there is no cached Stats for this chest_type.
    """
    from db_handler import Stats  # local import to avoid circular

    with _lock:
        entry = _statistics.get(chest_type)
        if entry is None or _now() > entry.expires_at:
            return
        old: Stats = entry.value
        new_count = old.total_chests + 1
        new_revenue = old.total_revenue + chest_revenue
        new_avg = new_revenue / new_count if new_count else 0.0
        new_stats = Stats(
            total_chests=new_count,
            total_revenue=new_revenue,
            avg_revenue_per_chest=new_avg,
        )
        store_entry = _Entry(value=new_stats, expires_at=entry.expires_at)
        _statistics[chest_type] = store_entry

    # Also invalidate loot rows so the table re-fetches with the new chest.
    with _lock:
        _loot_rows.pop(chest_type, None)
