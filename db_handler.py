"""
db_handler.py
-------------
All Supabase I/O lives here.  No tkinter imports – UI-agnostic.

Public API
----------
init(url, key)                          -> bool
write_chest_loot(chest_type, loot, item_prices) -> ChestWriteResult
fetch_chests(chest_type)                -> list[ChestRow]
fetch_loot_for_chest(chest_id)          -> list[LootRow]
calculate_statistics(chest_type, item_prices) -> Stats
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

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


def init(url: str, key: str) -> bool:
    """
    Initialise the Supabase client.  Call once at app startup.
    Returns True on success, False if credentials are missing or
    supabase-py is not installed.
    """
    global _client
    if not _SUPABASE_AVAILABLE:
        print("[db] supabase-py not installed — run: pip install supabase")
        return False
    if not url or not key or "YOUR_" in url or "YOUR_" in key:
        print("[db] Supabase credentials not configured in tracker_config.txt")
        return False
    try:
        assert create_client is not None
        _client = create_client(url, key)
        # Quick connectivity check
        _client.table("chests").select("id").limit(1).execute()
        print("[db] Connected to Supabase successfully")
        return True
    except Exception as exc:
        print(f"[db] Connection error: {exc}")
        _client = None
        return False


def is_connected() -> bool:
    return _client is not None


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
        # 1. Insert the chest record and get its new id
        chest_resp = _client.table("chests").insert({"chest_type": chest_type}).execute()
        chest_id: int = chest_resp.data[0]["id"]

        # 2. Insert all loot rows in one batch
        loot_rows = [{"chest_id": chest_id, "item_name": item.strip(), "quantity": qty} for qty, item in loot]
        _client.table("chest_loot").insert(loot_rows).execute()

        # 3. Count total chests of this type (for chest number display)
        count_resp = _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).execute()
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
    """Return all chests of *chest_type* ordered oldest-first."""
    if _client is None:
        return []
    try:
        resp = (
            _client.table("chests")
            .select("id, chest_type, recorded_at")
            .eq("chest_type", chest_type)
            .order("recorded_at")
            .execute()
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


def fetch_loot_for_chest(chest_id: int) -> list[LootRow]:
    """Return all loot rows for a single chest."""
    if _client is None:
        return []
    try:
        resp = (
            _client.table("chest_loot").select("id, chest_id, item_name, quantity").eq("chest_id", chest_id).execute()
        )
        return [
            LootRow(
                id=r["id"],
                chest_id=r["chest_id"],
                item_name=r["item_name"],
                quantity=r["quantity"],
            )
            for r in resp.data
        ]
    except Exception as exc:
        print(f"[db] fetch_loot_for_chest error: {exc}")
        return []


def fetch_all_loot(chest_type: str) -> list[dict]:
    """
    Return a flat list of dicts {chest_id, recorded_at, item_name, quantity}
    for all chests of *chest_type*.  Used for statistics and the viewer table.
    """
    if _client is None:
        return []
    try:
        resp = (
            _client.table("chest_loot")
            .select("chest_id, item_name, quantity, chests(chest_type, recorded_at)")
            .eq("chests.chest_type", chest_type)
            .execute()
        )
        results = []
        for r in resp.data:
            chest_info = r.get("chests") or {}
            if not chest_info or chest_info.get("chest_type") != chest_type:
                continue
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
    Fetches all loot from Supabase, groups by chest, sums revenue per chest.
    """
    if _client is None:
        return Stats()

    chests = fetch_chests(chest_type)
    if not chests:
        return Stats(total_chests=0)

    total_chests = len(chests)
    chest_ids = {c.id for c in chests}

    # Fetch all loot for these chests in one query
    try:
        resp = (
            _client.table("chest_loot")
            .select("chest_id, item_name, quantity")
            .in_("chest_id", list(chest_ids))
            .execute()
        )
        loot_rows = resp.data
    except Exception as exc:
        print(f"[db] calculate_statistics loot fetch error: {exc}")
        return Stats(total_chests=total_chests)

    # Sum revenue across all loot rows
    total_revenue = 0.0
    for row in loot_rows:
        item_key = row["item_name"].strip().lower()
        qty = row["quantity"]
        if item_key in item_prices:
            total_revenue += qty * item_prices[item_key]

    avg = total_revenue / total_chests if total_chests else 0.0
    return Stats(
        total_chests=total_chests,
        total_revenue=total_revenue,
        avg_revenue_per_chest=avg,
    )


# ---------------------------------------------------------------------------
# Streak calculation
# ---------------------------------------------------------------------------


def calculate_streak(chest_type: str, item_name: str) -> dict:
    """
    Calculate drop streaks for *item_name* across all chests of *chest_type*.

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

    chests = fetch_chests(chest_type)
    if not chests:
        return {}

    chest_ids = [c.id for c in chests]

    try:
        resp = (
            _client.table("chest_loot")
            .select("chest_id")
            .in_("chest_id", chest_ids)
            .eq("item_name", item_name)
            .execute()
        )
        chests_with_item = {r["chest_id"] for r in resp.data}
    except Exception as exc:
        print(f"[db] calculate_streak error: {exc}")
        return {}

    total_chests = len(chests)
    times_dropped = len(chests_with_item)
    longest = 0
    current = 0
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
    drop_rate_pct = chests_where_item_qty_gt_0 / total_chests * 100

    Uses server-side join + pagination to avoid Supabase URL length limits
    that silently truncate large .in_() ID lists.
    """
    if _client is None:
        return {}
    try:
        # Get total chest count server-side — no ID list needed
        count_resp = _client.table("chests").select("id", count="exact").eq("chest_type", chest_type).execute()
        total = count_resp.count or 0
        if total == 0:
            return {}

        from collections import defaultdict

        item_chests: dict[str, set[int]] = defaultdict(set)
        page_size = 1000
        offset = 0

        while True:
            resp = (
                _client.table("chest_loot")
                .select("chest_id, item_name, quantity, chests!inner(chest_type)")
                .eq("chests.chest_type", chest_type)
                .gt("quantity", 0)
                .range(offset, offset + page_size - 1)
                .execute()
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
    """
    Return loot rows only for the given chest IDs (used for session view).
    """
    if _client is None or not chest_ids:
        return []
    try:
        resp = (
            _client.table("chest_loot")
            .select("chest_id, item_name, quantity, chests(chest_type, recorded_at)")
            .in_("chest_id", chest_ids)
            .execute()
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
) -> "Stats":
    """Calculate stats for a specific set of chest IDs (session use)."""
    if _client is None or not chest_ids:
        return Stats()
    try:
        resp = _client.table("chest_loot").select("item_name, quantity").in_("chest_id", chest_ids).execute()
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
