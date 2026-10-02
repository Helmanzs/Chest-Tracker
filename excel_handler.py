"""Loot pivoting and .xlsx export. Price loading lives in prices_config.py."""

from __future__ import annotations

from datetime import datetime

import openpyxl
import pandas as pd

_META_COLUMNS = ["chest_id", "recorded_at"]


def pivot_loot(loot_rows: list[dict]) -> pd.DataFrame:
    """
    One row per chest, one column per item, with a leading 1-based '#' column.
    Rows need the keys: chest_id, recorded_at, item_name, quantity.
    """
    pivot = (
        pd.DataFrame(loot_rows)
        .pivot_table(
            index=_META_COLUMNS,
            columns="item_name",
            values="quantity",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    pivot.columns.name = None
    pivot.insert(0, "#", range(1, len(pivot) + 1))
    return pivot


def export_to_excel(
    chest_type: str,
    loot_rows: list[dict],
    drop_rates: dict[str, float] | None = None,
    column_order: list[str] | None = None,
    output_path: str | None = None,
) -> str:
    """
    Export loot rows to a .xlsx file and return the path it was saved to.

    drop_rates   : {item: drop %}, written to a second sheet if provided
    column_order : preferred item column order (as shown in the viewer)
    output_path  : explicit path; defaults to a timestamped filename
    """
    if not loot_rows:
        raise ValueError("No data to export")

    pivot = pivot_loot(loot_rows)

    if column_order:
        ordered = [c for c in column_order if c in pivot.columns and c not in _META_COLUMNS]
        remaining = [c for c in pivot.columns if c not in {"#", *_META_COLUMNS, *ordered}]
        pivot = pivot[["#", *_META_COLUMNS, *ordered, *remaining]]

    if output_path is None:
        safe_type = chest_type.replace("'", "").replace(" ", "_")
        output_path = f"{safe_type}_export_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = chest_type[:31]  # type: ignore[union-attr]
    ws.append(list(pivot.columns))  # type: ignore[union-attr]
    for row in pivot.itertuples(index=False):
        ws.append(list(row))  # type: ignore[union-attr]

    if drop_rates:
        _write_drop_rates_sheet(wb, pivot, drop_rates, column_order)

    wb.save(output_path)
    return output_path


def _write_drop_rates_sheet(
    wb: openpyxl.Workbook,
    pivot: pd.DataFrame,
    drop_rates: dict[str, float],
    column_order: list[str] | None,
) -> None:
    ws = wb.create_sheet(title="Drop Rates")
    ws.append(["Item", "Drop Rate %"])

    order = column_order or []

    def sort_key(item: str) -> tuple[float, int]:
        position = order.index(item) if item in order else 9999
        return (-drop_rates.get(item, 0.0), position)

    item_columns = [c for c in pivot.columns if c not in {"#", *_META_COLUMNS}]
    for item in sorted(item_columns, key=sort_key):
        rate = drop_rates.get(item)
        ws.append([item, "unknown" if rate is None else round(rate, 1)])
