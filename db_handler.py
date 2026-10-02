"""
All Supabase I/O. UI-agnostic.

Expensive reads (loot rows, drop rates, avg quantities, statistics) are served
from db_cache while fresh. fetch_all_stats_batch() computes everything for all
chest types in one scan at startup instead of several queries per chest type.
Writes invalidate the cache for the affected chest type.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import db_cache

try:
    from supabase import create_client

    _SUPABASE_AVAILABLE = True
except ImportError:
    create_client = None  # type: ignore[assignment]
    _SUPABASE_AVAILABLE = False

_client: Any = None
_client_lock = threading.Lock()  # serialise requests (HTTP/2 issues on Windows)
_SUPABASE_URL = ""
_SUPABASE_KEY = ""

_SOCKET_ERRORS = ("10035", "ReadError", "ConnectError")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ChestWriteResult:
    success: bool
    chest_id: int = 0
    chest_number: int = 0
    chest_revenue: float = 0.0
    most_expensive_item: tuple[str, float] = ("-", 0.0)
    error: str = ""


@dataclass
class Stats:
    total_chests: int = 0
    total_revenue: float = 0.0
    avg_revenue_per_chest: float = 0.0


@dataclass
class ChestRow:
    id: int
    chest_type: str
    recorded_at: str


# ---------------------------------------------------------------------------
# Connection & query helpers
# ---------------------------------------------------------------------------


def init(url: str, key: str) -> bool:
    global _client, _SUPABASE_URL, _SUPABASE_KEY
    if not _SUPABASE_AVAILABLE:
        print("[db] supabase-py not installed — run: pip install supabase")
        return False
    if not url or not key or "YOUR_" in url or "YOUR_" in key:
        print("[db] Supabase credentials not configured in tracker_config.txt")
        return False
    try:
        _SUPABASE_URL, _SUPABASE_KEY = url, key
        _client = create_client(url, key)
        _execute_with_retry(lambda: _client.table("chests").select("id").limit(1))
        db_cache.invalidate_all()
        print("[db] Connected to Supabase successfully")
        return True
    except Exception as exc:
        print(f"[db] Connection error: {exc}")
        _client = None
        return False


def is_connected() -> bool:
    return _client is not None


def _execute_with_retry(build_query, retries: int = 3):
    """Run a query, retrying (and eventually re-creating the client) on socket errors."""
    global _client
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with _client_lock:
                return build_query().execute()
        except Exception as exc:
            last_exc = exc
            if not any(marker in str(exc) for marker in _SOCKET_ERRORS):
                raise
            print(f"[db] socket error (attempt {attempt + 1}/{retries}): {exc}")
            time.sleep(0.5 * (attempt + 1))
            if attempt >= 1 and _SUPABASE_URL and create_client is not None:
                try:
                    with _client_lock:
                        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
                except Exception:
                    pass
    raise last_exc or RuntimeError("Query failed after retries")


def _paginate(make_query, page_size: int = 1000) -> list[dict]:
    """Fetch every row of a query (built by *make_query*) page by page."""
    results: list[dict] = []
    offset = 0
    while True:
        resp = _execute_with_retry(lambda o=offset: make_query().range(o, o + page_size - 1))
        rows = resp.data
        if not rows:
            break
        results.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return results


def _count_chests(chest_type: str) -> int:
    resp = _execute_with_retry(
        lambda: _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
    )
    return resp.count or 0


def _loot_query(chest_type: str, columns: str, chest_columns: str = "chest_type, is_valid"):
    """chest_loot rows joined to their valid chests of *chest_type*."""
    return (
        _client.table("chest_loot")
        .select(f"{columns}, chests!inner({chest_columns})")
        .eq("chests.chest_type", chest_type)
        .eq("chests.is_valid", True)
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_chest_loot(
    chest_type: str,
    loot: list[tuple[int, str]],
    item_prices: dict[str, float],
) -> ChestWriteResult:
    """*item_prices* must have lower-case item names as keys."""
    if _client is None:
        return ChestWriteResult(success=False, error="NOT_CONNECTED")

    try:
        chest_resp = _execute_with_retry(
            lambda: _client.table("chests").insert({"chest_type": chest_type, "is_valid": True})
        )
        chest_id: int = chest_resp.data[0]["id"]

        loot_rows = [{"chest_id": chest_id, "item_name": item.strip(), "quantity": qty} for qty, item in loot]
        _execute_with_retry(lambda: _client.table("chest_loot").insert(loot_rows))

        chest_number = _count_chests(chest_type)

        revenue = 0.0
        most_expensive: tuple[str, float] = ("-", 0.0)
        for qty, item in loot:
            price = item_prices.get(item.strip().lower())
            if price is None:
                continue
            value = qty * price
            revenue += value
            if value > most_expensive[1]:
                most_expensive = (item.strip(), value)

        db_cache.invalidate(chest_type)

        return ChestWriteResult(
            success=True,
            chest_id=chest_id,
            chest_number=chest_number,
            chest_revenue=revenue,
            most_expensive_item=most_expensive,
        )
    except Exception as exc:
        print(f"[db] write_chest_loot error: {exc}")
        return ChestWriteResult(success=False, error=str(exc))


# ---------------------------------------------------------------------------
# Reads (cached)
# ---------------------------------------------------------------------------


def fetch_chests(chest_type: str) -> list[ChestRow]:
    if _client is None:
        return []
    try:
        resp = _execute_with_retry(
            lambda: (
                _client.table("chests")
                .select("id, chest_type, recorded_at")
                .eq("chest_type", chest_type)
                .eq("is_valid", True)
                .order("recorded_at")
            )
        )
        return [ChestRow(id=r["id"], chest_type=r["chest_type"], recorded_at=r["recorded_at"]) for r in resp.data]
    except Exception as exc:
        print(f"[db] fetch_chests error: {exc}")
        return []


def fetch_all_loot(chest_type: str) -> list[dict]:
    cached = db_cache.get_loot_rows(chest_type)
    if cached is not None:
        return cached
    if _client is None:
        return []
    try:
        rows = _paginate(
            lambda: _loot_query(
                chest_type,
                "chest_id, item_name, quantity",
                chest_columns="chest_type, recorded_at, is_valid",
            )
        )
        results = [
            {
                "chest_id": r["chest_id"],
                "recorded_at": (r.get("chests") or {}).get("recorded_at", ""),
                "item_name": r["item_name"],
                "quantity": r["quantity"],
            }
            for r in rows
        ]
        db_cache.set_loot_rows(chest_type, results)
        return results
    except Exception as exc:
        print(f"[db] fetch_all_loot error: {exc}")
        return []


def calculate_statistics(chest_type: str, item_prices: dict[str, float]) -> Stats:
    """*item_prices* must have lower-case item names as keys."""
    cached = db_cache.get_statistics(chest_type)
    if cached is not None:
        return cached
    if _client is None:
        return Stats()
    try:
        total_chests = _count_chests(chest_type)
        if total_chests == 0:
            result = Stats()
        else:
            rows = _paginate(lambda: _loot_query(chest_type, "item_name, quantity"))
            revenue = _revenue(rows, item_prices)
            result = Stats(total_chests, revenue, revenue / total_chests)
        db_cache.set_statistics(chest_type, result)
        return result
    except Exception as exc:
        print(f"[db] calculate_statistics error: {exc}")
        return Stats()


def fetch_drop_rates(chest_type: str) -> dict[str, float]:
    """{item: % of chests in which the item dropped}."""
    cached = db_cache.get_drop_rates(chest_type)
    if cached is not None:
        return cached
    if _client is None:
        return {}
    try:
        total = _count_chests(chest_type)
        if total == 0:
            return {}

        rows = _paginate(lambda: _loot_query(chest_type, "chest_id, item_name, quantity").gt("quantity", 0))
        chests_per_item: dict[str, set[int]] = defaultdict(set)
        for r in rows:
            chests_per_item[r["item_name"]].add(r["chest_id"])

        result = {name: round(len(ids) / total * 100, 1) for name, ids in chests_per_item.items()}
        db_cache.set_drop_rates(chest_type, result)
        return result
    except Exception as exc:
        print(f"[db] fetch_drop_rates error: {exc}")
        return {}


def fetch_avg_quantities(chest_type: str) -> dict[str, float]:
    """{item: average quantity per drop}."""
    cached = db_cache.get_avg_quantities(chest_type)
    if cached is not None:
        return cached
    if _client is None:
        return {}
    try:
        if _count_chests(chest_type) == 0:
            return {}

        rows = _paginate(lambda: _loot_query(chest_type, "item_name, quantity").gt("quantity", 0))
        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            totals[r["item_name"]] += r["quantity"]
            counts[r["item_name"]] += 1

        result = {name: totals[name] / counts[name] for name in totals}
        db_cache.set_avg_quantities(chest_type, result)
        return result
    except Exception as exc:
        print(f"[db] fetch_avg_quantities error: {exc}")
        return {}


def _revenue(rows: list[dict], item_prices: dict[str, float]) -> float:
    return sum(
        r["quantity"] * item_prices[key] for r in rows if (key := r["item_name"].strip().lower()) in item_prices
    )


# ---------------------------------------------------------------------------
# Batched startup fetch
# ---------------------------------------------------------------------------


def fetch_all_stats_batch(
    chest_types: list[str],
    all_prices: dict[str, dict[str, float]],
) -> tuple[dict[str, Stats], dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """
    Compute statistics, drop rates and average quantities for ALL chest types
    from a single global loot scan. Populates the cache and returns
    (all_stats, all_drop_rates, all_avg_quantities).
    """
    if _client is None:
        return {ct: Stats() for ct in chest_types}, {}, {}

    wanted = set(chest_types)

    # Chest counts: one cheap query per type (skipped when stats are cached).
    chest_counts: dict[str, int] = {}
    for ct in chest_types:
        cached = db_cache.get_statistics(ct)
        if cached is not None:
            chest_counts[ct] = cached.total_chests
            continue
        try:
            chest_counts[ct] = _count_chests(ct)
        except Exception as exc:
            print(f"[db] batch count error for {ct}: {exc}")
            chest_counts[ct] = 0

    # Single loot scan across all chest types.
    try:
        loot_rows = _paginate(
            lambda: (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.is_valid", True)
                .gt("quantity", 0)
            )
        )
    except Exception as exc:
        print(f"[db] batch loot scan error: {exc}")
        loot_rows = []

    lower_prices = {ct: {k.lower(): v for k, v in prices.items()} for ct, prices in all_prices.items()}
    chests_per_item: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    qty_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    qty_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    revenue: dict[str, float] = defaultdict(float)

    for r in loot_rows:
        ct = (r.get("chests") or {}).get("chest_type", "")
        if ct not in wanted:
            continue
        item, qty = r["item_name"], r["quantity"]
        chests_per_item[ct][item].add(r["chest_id"])
        qty_totals[ct][item] += qty
        qty_counts[ct][item] += 1
        price = lower_prices.get(ct, {}).get(item.strip().lower())
        if price is not None:
            revenue[ct] += qty * price

    # Assemble results, keeping any entries that are already fresh in the cache.
    all_stats: dict[str, Stats] = {}
    all_drop_rates: dict[str, dict[str, float]] = {}
    all_avg_qty: dict[str, dict[str, float]] = {}

    for ct in chest_types:
        total = chest_counts[ct]

        drop_rates = db_cache.get_drop_rates(ct)
        if drop_rates is None:
            drop_rates = (
                {item: round(len(ids) / total * 100, 1) for item, ids in chests_per_item[ct].items()}
                if total > 0
                else {}
            )
            db_cache.set_drop_rates(ct, drop_rates)
        all_drop_rates[ct] = drop_rates

        avg_qty = db_cache.get_avg_quantities(ct)
        if avg_qty is None:
            avg_qty = {item: qty_totals[ct][item] / qty_counts[ct][item] for item in qty_totals[ct]}
            db_cache.set_avg_quantities(ct, avg_qty)
        all_avg_qty[ct] = avg_qty

        stats = db_cache.get_statistics(ct)
        if stats is None:
            stats = Stats(total, revenue[ct], revenue[ct] / total if total else 0.0)
            db_cache.set_statistics(ct, stats)
        all_stats[ct] = stats

    return all_stats, all_drop_rates, all_avg_qty


# ---------------------------------------------------------------------------
# Session-scoped reads (uncached; the chest ids are known locally)
# ---------------------------------------------------------------------------


def fetch_chests_by_ids(chest_ids: list[int]) -> list[dict]:
    if _client is None or not chest_ids:
        return []
    try:
        resp = _execute_with_retry(
            lambda: (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests(chest_type, recorded_at)")
                .in_("chest_id", chest_ids)
            )
        )
        return [
            {
                "chest_id": r["chest_id"],
                "recorded_at": (r.get("chests") or {}).get("recorded_at", ""),
                "item_name": r["item_name"],
                "quantity": r["quantity"],
            }
            for r in resp.data
        ]
    except Exception as exc:
        print(f"[db] fetch_chests_by_ids error: {exc}")
        return []


def calculate_statistics_for_ids(chest_ids: list[int], item_prices: dict[str, float]) -> Stats:
    if _client is None or not chest_ids:
        return Stats()
    try:
        resp = _execute_with_retry(
            lambda: _client.table("chest_loot").select("item_name, quantity").in_("chest_id", chest_ids)
        )
        revenue = _revenue(resp.data, item_prices)
        total = len(chest_ids)
        return Stats(total, revenue, revenue / total)
    except Exception as exc:
        print(f"[db] calculate_statistics_for_ids error: {exc}")
        return Stats()


# ---------------------------------------------------------------------------
# Per-item helpers
# ---------------------------------------------------------------------------


def fetch_item_avg(chest_type: str, item_name: str) -> float | None:
    cached = db_cache.get_avg_quantities(chest_type)
    if cached is not None:
        return cached.get(item_name)
    if _client is None:
        return None
    try:
        total_chests = _count_chests(chest_type)
        if total_chests == 0:
            return None

        resp = _execute_with_retry(
            lambda: _loot_query(chest_type, "quantity").eq("item_name", item_name).gt("quantity", 0)
        )
        if not resp.data:
            return None
        return sum(r["quantity"] for r in resp.data) / total_chests
    except Exception as exc:
        print(f"[db] fetch_item_avg error: {exc}")
        return None


def calculate_streak(chest_type: str, item_name: str) -> dict:
    """Current/longest dry streak (chests without *item_name*) plus drop stats."""
    if _client is None:
        return {}

    chests = fetch_chests(chest_type)
    if not chests:
        return {}

    chest_ids = [c.id for c in chests]
    try:
        resp = _execute_with_retry(
            lambda: _client.table("chest_loot")
            .select("chest_id")
            .in_("chest_id", chest_ids)
            .eq("item_name", item_name)
        )
    except Exception as exc:
        print(f"[db] calculate_streak error: {exc}")
        return {}

    chests_with_item = {r["chest_id"] for r in resp.data}
    longest = run = 0
    for chest in chests:
        if chest.id in chests_with_item:
            longest = max(longest, run)
            run = 0
        else:
            run += 1
    longest = max(longest, run)

    total_chests = len(chests)
    times_dropped = len(chests_with_item)
    return {
        "current_streak": run,
        "longest_streak": longest,
        "total_chests": total_chests,
        "times_dropped": times_dropped,
        "drop_rate_pct": round(times_dropped / total_chests * 100, 1),
    }
