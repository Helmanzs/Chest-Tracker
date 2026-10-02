"""
TTL-based in-memory cache for expensive Supabase reads, keyed by chest type.
Writes call invalidate() so the next read re-fetches. Thread-safe.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

CACHE_TTL = 120.0  # seconds

_lock = threading.RLock()


@dataclass
class _Entry:
    value: Any
    expires_at: float


_drop_rates: dict[str, _Entry] = {}
_avg_quantities: dict[str, _Entry] = {}
_statistics: dict[str, _Entry] = {}
_loot_rows: dict[str, _Entry] = {}

_ALL_STORES = (_drop_rates, _avg_quantities, _statistics, _loot_rows)


def _get(store: dict[str, _Entry], key: str) -> Any | None:
    with _lock:
        entry = store.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            return None
        return entry.value


def _set(store: dict[str, _Entry], key: str, value: Any) -> None:
    with _lock:
        store[key] = _Entry(value, time.monotonic() + CACHE_TTL)


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


def invalidate(chest_type: str) -> None:
    """Expire all cached data for *chest_type* (call after writing to it)."""
    with _lock:
        for store in _ALL_STORES:
            store.pop(chest_type, None)


def invalidate_all() -> None:
    with _lock:
        for store in _ALL_STORES:
            store.clear()
