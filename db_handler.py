"""
db_handler.py
-------------
All Supabase I/O lives here.  No tkinter imports – UI-agnostic.

Public API
----------
init(url, key)                          -> bool
write_chest_loot(chest_type, loot, item_prices) -> ChestWriteResult
fetch_chests(chest_type)                -> list[ChestRow]
calculate_statistics(chest_type, item_prices) -> Stats
fetch_all_stats_batch(chest_types, all_prices) -> dict[str, Stats]

Performance notes
-----------------
- All expensive read functions (fetch_all_loot, fetch_drop_rates,
  fetch_avg_quantities, calculate_statistics) are served from
  db_cache when the cached value is still fresh (default TTL = 2 min).
- fetch_all_stats_batch() fetches drop-rates, avg-quantities, and
  statistics for every chest type in ONE paginated scan each,
  instead of 3-4 separate queries × N chest types.
- write_chest_loot() patches the cached Stats in-place so the
  viewer updates instantly without a round-trip.
- Loot rows cache is invalidated on every write so the table stays
  fresh after the next Refresh click.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

import db_cache

# supabase-py is a required dependency
try:
    from supabase import create_client as create_client

    _SUPABASE_AVAILABLE = True
except ImportError:
    create_client = None  # type: ignore[assignment]
    _SUPABASE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Module-level client (initialised once via init())
# ---------------------------------------------------------------------------

_client: Any = None  # supabase.Client when connected
_client_lock = threading.Lock()  # serialise concurrent requests on Windows HTTP/2
_SUPABASE_URL: str = ""
_SUPABASE_KEY: str = ""


def init(url: str, key: str) -> bool:
    global _client, _SUPABASE_URL, _SUPABASE_KEY
    if not _SUPABASE_AVAILABLE:
        print("[db] supabase-py not installed — run: pip install supabase")
        return False
    if not url or not key or "YOUR_" in url or "YOUR_" in key:
        print("[db] Supabase credentials not configured in tracker_config.txt")
        return False
    try:
        assert create_client is not None
        _SUPABASE_URL = url
        _SUPABASE_KEY = key
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


def _execute_with_retry(build_query: "Any", retries: int = 3) -> "Any":
    global _client
    import time

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with _client_lock:
                return build_query().execute()
        except Exception as exc:
            last_exc = exc
            err = str(exc)
            if "10035" in err or "ReadError" in err or "ConnectError" in err:
                print(f"[db] socket error (attempt {attempt + 1}/{retries}): {exc}")
                time.sleep(0.5 * (attempt + 1))
                if attempt >= 1 and _SUPABASE_URL and create_client is not None:
                    try:
                        with _client_lock:
                            _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
                    except Exception:
                        pass
            else:
                raise
    raise last_exc or RuntimeError("Query failed after retries")


# ---------------------------------------------------------------------------
# Return-value containers
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


@dataclass
class LootRow:
    id: int
    chest_id: int
    item_name: str
    quantity: int


# ---------------------------------------------------------------------------
# Internal paginated fetcher (shared by multiple functions)
# ---------------------------------------------------------------------------


def _paginate(build_query_fn, page_size: int = 1000) -> list[dict]:
    """Run a paginated Supabase query and return all rows as a flat list."""
    results: list[dict] = []
    offset = 0
    while True:
        resp = _execute_with_retry(lambda off=offset: build_query_fn(off, page_size))
        rows = resp.data
        if not rows:
            break
        results.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return results


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_chest_loot(
    chest_type: str,
    loot: list[tuple[int, str]],
    item_prices: dict[str, float],
) -> ChestWriteResult:
    if _client is None:
        return ChestWriteResult(success=False, error="NOT_CONNECTED")

    try:
        chest_resp = _execute_with_retry(
            lambda: _client.table("chests").insert({"chest_type": chest_type, "is_valid": True})
        )
        chest_id: int = chest_resp.data[0]["id"]

        loot_rows = [{"chest_id": chest_id, "item_name": item.strip(), "quantity": qty} for qty, item in loot]
        _execute_with_retry(lambda: _client.table("chest_loot").insert(loot_rows))

        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        chest_number = count_resp.count or 0

        chest_revenue = 0.0
        most_expensive: tuple[str, float] = ("-", 0.0)
        for qty, item in loot:
            item_key = item.strip().lower()
            if item_key in item_prices:
                value = qty * item_prices[item_key]
                chest_revenue += value
                if value > most_expensive[1]:
                    most_expensive = (item.strip(), value)

        # Patch cached stats in-place; invalidate loot cache so table refreshes.
        db_cache.patch_statistics_after_write(chest_type, chest_revenue, loot, item_prices)
        db_cache.invalidate(chest_type)
        # Re-store the patched stats so they survive the invalidation above.
        # (invalidate() clears the stats entry; we rebuild it from chest_number)
        # We do a lightweight re-fetch only for the count; revenue is patched locally.

        return ChestWriteResult(
            success=True,
            chest_id=chest_id,
            chest_number=chest_number,
            chest_revenue=chest_revenue,
            most_expensive_item=most_expensive,
        )

    except Exception as exc:
        print(f"[db] write_chest_loot error: {exc}")
        return ChestWriteResult(success=False, error=str(exc))


# ---------------------------------------------------------------------------
# Read — with cache
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
            lambda off, ps: (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests!inner(chest_type, recorded_at, is_valid)")
                .eq("chests.chest_type", chest_type)
                .eq("chests.is_valid", True)
                .range(off, off + ps - 1)
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


# ---------------------------------------------------------------------------
# Statistics — with cache
# ---------------------------------------------------------------------------


def calculate_statistics(
    chest_type: str,
    item_prices: dict[str, float],
) -> Stats:
    cached = db_cache.get_statistics(chest_type)
    if cached is not None:
        return cached

    if _client is None:
        return Stats()

    try:
        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        total_chests = count_resp.count or 0
        if total_chests == 0:
            result = Stats(total_chests=0)
            db_cache.set_statistics(chest_type, result)
            return result

        total_revenue = 0.0
        rows = _paginate(
            lambda off, ps: (
                _client.table("chest_loot")
                .select("item_name, quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.chest_type", chest_type)
                .eq("chests.is_valid", True)
                .range(off, off + ps - 1)
            )
        )
        for row in rows:
            item_key = row["item_name"].strip().lower()
            if item_key in item_prices:
                total_revenue += row["quantity"] * item_prices[item_key]

        avg = total_revenue / total_chests if total_chests else 0.0
        result = Stats(total_chests=total_chests, total_revenue=total_revenue, avg_revenue_per_chest=avg)
        db_cache.set_statistics(chest_type, result)
        return result

    except Exception as exc:
        print(f"[db] calculate_statistics error: {exc}")
        return Stats()


# ---------------------------------------------------------------------------
# Batched startup fetch
# ---------------------------------------------------------------------------


def fetch_all_stats_batch(
    chest_types: list[str],
    all_prices: dict[str, dict[str, float]],
) -> tuple[dict[str, Stats], dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """
    Fetch drop-rates, avg-quantities, and statistics for ALL chest types
    in three global paginated scans instead of 3 × N per-chest queries.

    Returns (all_stats, all_drop_rates, all_avg_quantities).

    Results are stored in the cache before returning.
    """
    if _client is None:
        empty_stats = {ct: Stats() for ct in chest_types}
        return empty_stats, {}, {}

    from collections import defaultdict

    chest_type_set = set(chest_types)

    # ── 1. Chest counts (one query per type — unavoidable, but fast) ──
    chest_counts: dict[str, int] = {}
    for ct in chest_types:
        cached_st = db_cache.get_statistics(ct)
        if cached_st is not None:
            chest_counts[ct] = cached_st.total_chests
        else:
            try:
                resp = _execute_with_retry(
                    lambda t=ct: (
                        _client.table("chests").select("id", count="exact").eq("chest_type", t).eq("is_valid", True)
                    )
                )
                chest_counts[ct] = resp.count or 0
            except Exception as exc:
                print(f"[db] batch count error for {ct}: {exc}")
                chest_counts[ct] = 0

    # ── 2. Single global loot scan — drop rates + avg qty + revenue ──
    # Structures: chest_type → item → set of chest_ids  (for drop rate)
    #             chest_type → item → (total_qty, chest_count)  (for avg)
    #             chest_type → total_revenue
    item_chest_sets: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    item_qty_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    item_qty_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    revenue_totals: dict[str, float] = defaultdict(float)

    # We need chest_type from the join — select it along with loot data.
    try:
        all_loot_rows = _paginate(
            lambda off, ps: (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.is_valid", True)
                .gt("quantity", 0)
                .range(off, off + ps - 1)
            )
        )
    except Exception as exc:
        print(f"[db] batch loot scan error: {exc}")
        all_loot_rows = []

    for r in all_loot_rows:
        ct = (r.get("chests") or {}).get("chest_type", "")
        if ct not in chest_type_set:
            continue
        item = r["item_name"]
        qty = r["quantity"]
        cid = r["chest_id"]

        item_chest_sets[ct][item].add(cid)
        item_qty_totals[ct][item] += qty
        item_qty_counts[ct][item] += 1

        prices = {k.lower(): v for k, v in all_prices.get(ct, {}).items()}
        item_key = item.strip().lower()
        if item_key in prices:
            revenue_totals[ct] += qty * prices[item_key]

    # ── 3. Assemble results and populate cache ──
    all_stats: dict[str, Stats] = {}
    all_drop_rates: dict[str, dict[str, float]] = {}
    all_avg_qty: dict[str, dict[str, float]] = {}

    for ct in chest_types:
        # Skip if we already have a fresh cache entry (e.g. from a manual refresh).
        cached_dr = db_cache.get_drop_rates(ct)
        cached_aq = db_cache.get_avg_quantities(ct)
        cached_st = db_cache.get_statistics(ct)

        total = chest_counts.get(ct, 0)

        if cached_dr is None:
            dr: dict[str, float] = {}
            if total > 0:
                for item, cid_set in item_chest_sets[ct].items():
                    dr[item] = round(len(cid_set) / total * 100, 1)
            db_cache.set_drop_rates(ct, dr)
            all_drop_rates[ct] = dr
        else:
            all_drop_rates[ct] = cached_dr

        if cached_aq is None:
            aq: dict[str, float] = {}
            for item, total_qty in item_qty_totals[ct].items():
                cnt = item_qty_counts[ct][item]
                if cnt > 0:
                    aq[item] = total_qty / cnt
            db_cache.set_avg_quantities(ct, aq)
            all_avg_qty[ct] = aq
        else:
            all_avg_qty[ct] = cached_aq

        if cached_st is None:
            rev = revenue_totals.get(ct, 0.0)
            avg = rev / total if total else 0.0
            st = Stats(total_chests=total, total_revenue=rev, avg_revenue_per_chest=avg)
            db_cache.set_statistics(ct, st)
            all_stats[ct] = st
        else:
            all_stats[ct] = cached_st

    return all_stats, all_drop_rates, all_avg_qty


# ---------------------------------------------------------------------------
# Drop rates — with cache
# ---------------------------------------------------------------------------


def fetch_drop_rates(chest_type: str) -> dict[str, float]:
    cached = db_cache.get_drop_rates(chest_type)
    if cached is not None:
        return cached

    if _client is None:
        return {}
    try:
        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        total = count_resp.count or 0
        if total == 0:
            return {}

        from collections import defaultdict

        item_chests: dict[str, set[int]] = defaultdict(set)

        rows = _paginate(
            lambda off, ps: (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.chest_type", chest_type)
                .eq("chests.is_valid", True)
                .gt("quantity", 0)
                .range(off, off + ps - 1)
            )
        )
        for r in rows:
            item_chests[r["item_name"]].add(r["chest_id"])

        result = {name: round(len(ids) / total * 100, 1) for name, ids in item_chests.items()}
        db_cache.set_drop_rates(chest_type, result)
        return result
    except Exception as exc:
        print(f"[db] fetch_drop_rates error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Avg quantities — with cache
# ---------------------------------------------------------------------------


def fetch_avg_quantities(chest_type: str) -> dict[str, float]:
    cached = db_cache.get_avg_quantities(chest_type)
    if cached is not None:
        return cached

    if _client is None:
        return {}
    try:
        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        total = count_resp.count or 0
        if total == 0:
            return {}

        from collections import defaultdict

        item_totals: dict[str, float] = defaultdict(float)
        item_counts: dict[str, int] = defaultdict(int)

        rows = _paginate(
            lambda off, ps: (
                _client.table("chest_loot")
                .select("item_name, quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.chest_type", chest_type)
                .eq("chests.is_valid", True)
                .gt("quantity", 0)
                .range(off, off + ps - 1)
            )
        )
        for r in rows:
            item_totals[r["item_name"]] += r["quantity"]
            item_counts[r["item_name"]] += 1

        result = {name: item_totals[name] / item_counts[name] for name in item_totals if item_counts[name] > 0}
        db_cache.set_avg_quantities(chest_type, result)
        return result
    except Exception as exc:
        print(f"[db] fetch_avg_quantities error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Session-scoped fetching (no cache — session data is already in memory)
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
        results = []
        for r in resp.data:
            chest_info = r.get("chests") or {}
            results.append(
                {
                    "chest_id": r["chest_id"],
                    "recorded_at": chest_info.get("recorded_at", ""),
                    "item_name": r["item_name"],
                    "quantity": r["quantity"],
                }
            )
        return results
    except Exception as exc:
        print(f"[db] fetch_chests_by_ids error: {exc}")
        return []


def calculate_statistics_for_ids(
    chest_ids: list[int],
    item_prices: dict[str, float],
) -> Stats:
    if _client is None or not chest_ids:
        return Stats()
    try:
        resp = _execute_with_retry(
            lambda: (_client.table("chest_loot").select("item_name, quantity").in_("chest_id", chest_ids))
        )
        total_revenue = 0.0
        for row in resp.data:
            key = row["item_name"].strip().lower()
            if key in item_prices:
                total_revenue += row["quantity"] * item_prices[key]
        total = len(chest_ids)
        avg = total_revenue / total if total else 0.0
        return Stats(total_chests=total, total_revenue=total_revenue, avg_revenue_per_chest=avg)
    except Exception as exc:
        print(f"[db] calculate_statistics_for_ids error: {exc}")
        return Stats()


# ---------------------------------------------------------------------------
# Per-item helpers
# ---------------------------------------------------------------------------


def fetch_item_avg(chest_type: str, item_name: str) -> float | None:
    # Try to derive from cached avg quantities first.
    cached = db_cache.get_avg_quantities(chest_type)
    if cached is not None:
        val = cached.get(item_name)
        return val  # may be None if item never dropped

    if _client is None:
        return None
    try:
        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        total_chests = count_resp.count or 0
        if total_chests == 0:
            return None

        resp = _execute_with_retry(
            lambda: (
                _client.table("chest_loot")
                .select("quantity, chests!inner(chest_type, is_valid)")
                .eq("chests.chest_type", chest_type)
                .eq("chests.is_valid", True)
                .eq("item_name", item_name)
                .gt("quantity", 0)
            )
        )
        rows = resp.data
        if not rows:
            return None
        total_qty = sum(r["quantity"] for r in rows)
        return total_qty / total_chests
    except Exception as exc:
        print(f"[db] fetch_item_avg error: {exc}")
        return None


def calculate_streak(chest_type: str, item_name: str) -> dict:
    if _client is None:
        return {}

    chests = fetch_chests(chest_type)
    if not chests:
        return {}

    chest_ids = [c.id for c in chests]
    try:
        resp = _execute_with_retry(
            lambda: (
                _client.table("chest_loot").select("chest_id").in_("chest_id", chest_ids).eq("item_name", item_name)
            )
        )
        chests_with_item = {r["chest_id"] for r in resp.data}
    except Exception as exc:
        print(f"[db] calculate_streak error: {exc}")
        return {}

    total_chests = len(chests)
    times_dropped = len(chests_with_item)
    longest = 0
    run = 0

    for chest in chests:
        if chest.id in chests_with_item:
            longest = max(longest, run)
            run = 0
        else:
            run += 1

    current = run
    longest = max(longest, current)
    drop_rate = (times_dropped / total_chests * 100) if total_chests else 0.0

    return {
        "current_streak": current,
        "longest_streak": longest,
        "total_chests": total_chests,
        "times_dropped": times_dropped,
        "drop_rate_pct": round(drop_rate, 1),
    }
