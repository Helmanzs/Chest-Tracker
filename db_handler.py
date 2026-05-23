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
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

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
    """
    Initialise the Supabase client.  Call once at app startup.
    Returns True on success, False if credentials are missing or
    supabase-py is not installed.
    """
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
        # Quick connectivity check
        _execute_with_retry(lambda: _client.table("chests").select("id").limit(1))
        print("[db] Connected to Supabase successfully")
        return True
    except Exception as exc:
        print(f"[db] Connection error: {exc}")
        _client = None
        return False


def is_connected() -> bool:
    return _client is not None


def _execute_with_retry(build_query: "Any", retries: int = 3) -> "Any":
    """
    Execute a Supabase query with retries and a lock to prevent
    concurrent HTTP/2 socket issues on Windows.
    """
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
            # WinError 10035 = socket would block; reconnect and retry
            if "10035" in err or "ReadError" in err or "ConnectError" in err:
                print(f"[db] socket error (attempt {attempt + 1}/{retries}): {exc}")
                time.sleep(0.5 * (attempt + 1))
                # Recreate client on persistent socket errors
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
    chest_number: int = 0  # total chests of this type so far
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
# Write
# ---------------------------------------------------------------------------


def write_chest_loot(
    chest_type: str,
    loot: list[tuple[int, str]],
    item_prices: dict[str, float],
) -> ChestWriteResult:
    """
    Insert one chest + its loot rows into Supabase.

    Parameters
    ----------
    chest_type  : display name e.g. "Razador's Chest"
    loot        : list of (quantity, item_name) tuples
    item_prices : local price lookup dict (lowercase keys)
    """
    if _client is None:
        return ChestWriteResult(success=False, error="NOT_CONNECTED")

    try:
        # 1. Insert the chest record with is_valid=True and get its new id
        chest_resp = _execute_with_retry(
            lambda: _client.table("chests").insert({"chest_type": chest_type, "is_valid": True})
        )
        chest_id: int = chest_resp.data[0]["id"]

        # 2. Insert all loot rows in one batch
        loot_rows = [{"chest_id": chest_id, "item_name": item.strip(), "quantity": qty} for qty, item in loot]
        _execute_with_retry(lambda: _client.table("chest_loot").insert(loot_rows))

        # 3. Count total valid chests of this type (for chest number display)
        count_resp = _execute_with_retry(
            lambda: (
                _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True)
            )
        )
        chest_number = count_resp.count or 0

        # 4. Calculate revenue locally using prices
        chest_revenue = 0.0
        most_expensive: tuple[str, float] = ("-", 0.0)
        for qty, item in loot:
            item_key = item.strip().lower()
            if item_key in item_prices:
                value = qty * item_prices[item_key]
                chest_revenue += value
                if value > most_expensive[1]:
                    most_expensive = (item.strip(), value)

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
# Read
# ---------------------------------------------------------------------------


def fetch_chests(chest_type: str) -> list[ChestRow]:
    """Return all valid chests of *chest_type* ordered oldest-first."""
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
        return [
            ChestRow(
                id=r["id"],
                chest_type=r["chest_type"],
                recorded_at=r["recorded_at"],
            )
            for r in resp.data
        ]
    except Exception as exc:
        print(f"[db] fetch_chests error: {exc}")
        return []


def fetch_all_loot(chest_type: str) -> list[dict]:
    """
    Return a flat list of dicts {chest_id, recorded_at, item_name, quantity}
    for all valid chests of *chest_type*.

    Uses !inner join so Supabase filters server-side — avoids the bug where
    a regular join returns all rows with null chest_info for non-matching rows.
    Paginates to handle large datasets.
    """
    if _client is None:
        return []
    try:
        results: list[dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = _execute_with_retry(
                lambda off=offset: (
                    _client.table("chest_loot")
                    .select("chest_id, item_name, quantity, chests!inner(chest_type, recorded_at, is_valid)")
                    .eq("chests.chest_type", chest_type)
                    .eq("chests.is_valid", True)
                    .range(off, off + page_size - 1)
                )
            )
            rows = resp.data
            if not rows:
                break
            for r in rows:
                chest_info = r.get("chests") or {}
                results.append(
                    {
                        "chest_id": r["chest_id"],
                        "recorded_at": chest_info.get("recorded_at", ""),
                        "item_name": r["item_name"],
                        "quantity": r["quantity"],
                    }
                )
            if len(rows) < page_size:
                break
            offset += page_size
        return results
    except Exception as exc:
        print(f"[db] fetch_all_loot error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def calculate_statistics(
    chest_type: str,
    item_prices: dict[str, float],
) -> Stats:
    """
    Compute aggregate revenue statistics for *chest_type* using local prices.
    Only counts valid chests.
    Uses !inner join + pagination — no large ID lists passed over the wire.
    """
    if _client is None:
        return Stats()

    # Get total valid chest count
    count_resp = _execute_with_retry(
        lambda: (_client.table("chests").select("id", count="exact").eq("chest_type", chest_type).eq("is_valid", True))
    )
    total_chests = count_resp.count or 0
    if total_chests == 0:
        return Stats(total_chests=0)

    # Fetch all loot via server-side join (valid chests only), paginated
    total_revenue = 0.0
    page_size = 1000
    offset = 0
    while True:
        try:
            resp = _execute_with_retry(
                lambda off=offset: (
                    _client.table("chest_loot")
                    .select("item_name, quantity, chests!inner(chest_type, is_valid)")
                    .eq("chests.chest_type", chest_type)
                    .eq("chests.is_valid", True)
                    .range(off, off + page_size - 1)
                )
            )
        except Exception as exc:
            print(f"[db] calculate_statistics fetch error: {exc}")
            break
        rows = resp.data
        if not rows:
            break
        for row in rows:
            item_key = row["item_name"].strip().lower()
            qty = row["quantity"]
            if item_key in item_prices:
                total_revenue += qty * item_prices[item_key]
        if len(rows) < page_size:
            break
        offset += page_size

    avg = total_revenue / total_chests if total_chests else 0.0
    return Stats(
        total_chests=total_chests,
        total_revenue=total_revenue,
        avg_revenue_per_chest=avg,
    )


def calculate_streak(chest_type: str, item_name: str) -> dict:
    """
    Calculate drop streaks for *item_name* across all valid chests of *chest_type*.

    Returns
    -------
    {
      "current_streak": int,   # chests since last drop (0 = dropped in last chest)
      "longest_streak": int,   # longest run of chests without this item
      "total_chests": int,
      "times_dropped": int,
      "drop_rate_pct": float,
    }
    """
    if _client is None:
        return {}

    chests = fetch_chests(chest_type)  # already filters is_valid=True
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

    # current streak = run at end of list
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


# ---------------------------------------------------------------------------
# Drop rates
# ---------------------------------------------------------------------------


def fetch_drop_rates(chest_type: str) -> dict[str, float]:
    """
    Return {item_name: drop_rate_pct} for *chest_type*.
    drop_rate_pct = chests_where_item_qty_gt_0 / total_valid_chests * 100

    Uses server-side join + pagination to avoid Supabase URL length limits
    that silently truncate large .in_() ID lists.
    """
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
        page_size = 1000
        offset = 0

        while True:
            resp = _execute_with_retry(
                lambda off=offset: (
                    _client.table("chest_loot")
                    .select("chest_id, item_name, quantity, chests!inner(chest_type, is_valid)")
                    .eq("chests.chest_type", chest_type)
                    .eq("chests.is_valid", True)
                    .gt("quantity", 0)
                    .range(off, off + page_size - 1)
                )
            )
            rows = resp.data
            if not rows:
                break
            for r in rows:
                item_chests[r["item_name"]].add(r["chest_id"])
            if len(rows) < page_size:
                break
            offset += page_size

        return {name: round(len(ids) / total * 100, 1) for name, ids in item_chests.items()}
    except Exception as exc:
        print(f"[db] fetch_drop_rates error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Session-scoped fetching
# ---------------------------------------------------------------------------


def fetch_chests_by_ids(chest_ids: list[int]) -> list[dict]:
    """Return loot rows only for the given chest IDs (used for session view).
    These are already known-valid IDs written by this session, so no
    additional is_valid filter is needed here."""
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
    """Calculate stats for a specific set of chest IDs (session use).
    IDs come from the current session so they are already valid."""
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
        return Stats(
            total_chests=total,
            total_revenue=total_revenue,
            avg_revenue_per_chest=avg,
        )
    except Exception as exc:
        print(f"[db] calculate_statistics_for_ids error: {exc}")
        return Stats()


# ---------------------------------------------------------------------------
# Per-item average quantity
# ---------------------------------------------------------------------------


def fetch_item_avg(chest_type: str, item_name: str) -> float | None:
    """
    Return the average quantity of *item_name* per chest for *chest_type*,
    counting only valid chests where the item actually dropped (quantity > 0).
    Returns None if there is no data yet.
    """
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


# ---------------------------------------------------------------------------
# Average quantity per item
# ---------------------------------------------------------------------------


def fetch_avg_quantities(chest_type: str) -> dict[str, float]:
    """
    Return {item_name: avg_qty_per_chest} for all items in *chest_type*.
    avg = total_quantity / chests_where_item_dropped (excludes zeros).
    Only counts valid chests.
    """
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
        page_size = 1000
        offset = 0
        while True:
            resp = _execute_with_retry(
                lambda off=offset: (
                    _client.table("chest_loot")
                    .select("item_name, quantity, chests!inner(chest_type, is_valid)")
                    .eq("chests.chest_type", chest_type)
                    .eq("chests.is_valid", True)
                    .gt("quantity", 0)
                    .range(off, off + page_size - 1)
                )
            )
            rows = resp.data
            if not rows:
                break
            for r in rows:
                item_totals[r["item_name"]] += r["quantity"]
                item_counts[r["item_name"]] += 1
            if len(rows) < page_size:
                break
            offset += page_size

        return {name: item_totals[name] / item_counts[name] for name in item_totals if item_counts[name] > 0}
    except Exception as exc:
        print(f"[db] fetch_avg_quantities error: {exc}")
        return {}
