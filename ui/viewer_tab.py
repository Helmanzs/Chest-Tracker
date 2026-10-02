"""The "Excel Data" tab: chest selector, session toggle, export, statistics and a data table."""

from __future__ import annotations

from typing import Callable

import dearpygui.dearpygui as dpg
import pandas as pd

import db_handler
from ui.theme import GREEN, color_button
from utils import fmt_number

# Columns always shown first, in this order.
PINNED_COLUMNS = ["#", "chest_id", "recorded_at", "Shard", "Energy Fragment"]

_ROW_EVEN = (50, 50, 55, 255)
_ROW_ODD = (42, 42, 48, 255)
_TABLE_TAG = "viewer_data_table"
_EMPTY_MSG_TAG = "_viewer_empty_msg"


class ViewerTab:
    def __init__(
        self,
        parent_tag: str | int,
        chest_types: list[str],
        on_refresh: Callable[[], None],
        on_reload_prices: Callable[[], None],
        on_export: Callable[[], None],
        on_session_toggle: Callable[[bool], None],
        on_chest_selected: Callable[[str], None],
        initial_chest: str = "",
    ) -> None:
        self._parent = parent_tag
        self._chest_types = chest_types
        self._on_refresh = on_refresh
        self._on_reload_prices = on_reload_prices
        self._on_export = on_export
        self._on_session_toggle = on_session_toggle
        self._on_chest_selected = on_chest_selected

        self._selected_chest = (
            initial_chest if initial_chest in chest_types else (chest_types[0] if chest_types else "")
        )
        self._session_mode = False
        self._ids: dict[str, int | str] = {}

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        with dpg.group(parent=self._parent):
            with dpg.group(horizontal=True):
                dpg.add_text("Chest:", indent=4)
                self._ids["chest_combo"] = dpg.add_combo(
                    items=self._chest_types,
                    default_value=self._selected_chest,
                    width=280,
                    label="",
                    callback=self._on_combo,
                )
                dpg.add_spacer(width=16)
                dpg.add_checkbox(
                    label="Show current session only",
                    default_value=False,
                    callback=self._on_checkbox,
                )

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh Data", callback=self._on_refresh)
                dpg.add_spacer(width=4)
                dpg.add_button(label="Reload Prices", callback=self._on_reload_prices)
                dpg.add_spacer(width=4)
                export_btn = dpg.add_button(label="Export to Excel", callback=self._on_export)
                color_button(export_btn, GREEN)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            self._build_statistics()

            dpg.add_spacer(height=6)
            self._ids["table_container"] = dpg.add_child_window(
                width=-1,
                height=-1,
                horizontal_scrollbar=True,
                border=True,
            )

    def _build_statistics(self) -> None:
        with dpg.collapsing_header(label="Statistics", default_open=True):
            with dpg.table(header_row=False, borders_innerV=False, borders_outerV=False):
                for width in (150, 180, 150, 220):
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=width)

                with dpg.table_row():
                    dpg.add_text("Chests:")
                    self._ids["total_chests"] = dpg.add_text("0", color=(100, 140, 255, 255))
                    dpg.add_text("Revenue/Chest:")
                    self._ids["rev_per_chest"] = dpg.add_text("N/A", color=(80, 200, 100, 255))

                with dpg.table_row():
                    dpg.add_text("Total Revenue:")
                    self._ids["total_rev"] = dpg.add_text("N/A", color=(80, 200, 100, 255))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def selected_chest(self) -> str:
        return self._selected_chest

    def is_session_mode(self) -> bool:
        return self._session_mode

    def set_selected_chest(self, chest_type: str) -> None:
        if chest_type not in self._chest_types:
            return
        self._selected_chest = chest_type
        if dpg.does_item_exist(self._ids["chest_combo"]):
            dpg.set_value(self._ids["chest_combo"], chest_type)

    def set_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        combo = self._ids["chest_combo"]
        if not dpg.does_item_exist(combo):
            return
        dpg.configure_item(combo, items=chest_types)
        if chest_types and not self._selected_chest:
            self._selected_chest = chest_types[0]
            dpg.set_value(combo, chest_types[0])

    def load_dataframe(self, df: pd.DataFrame, item_prices: dict[str, float] | None = None) -> None:
        container = self._ids.get("table_container")
        if not container or not dpg.does_item_exist(container):
            return

        for tag in (_TABLE_TAG, _EMPTY_MSG_TAG):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

        if df.empty:
            with dpg.group(parent=container, tag=_EMPTY_MSG_TAG):
                dpg.add_spacer(height=10)
                dpg.add_text("No data yet -- start tracking chests!", color=(160, 160, 160, 255))
            return

        columns = self._sort_columns(list(df.columns), item_prices or {})
        df = df[columns]

        with dpg.table(
            tag=_TABLE_TAG,
            parent=container,
            header_row=True,
            row_background=True,
            borders_outerH=True,
            borders_innerH=True,
            borders_innerV=True,
            borders_outerV=True,
            scrollX=True,
            scrollY=True,
            resizable=True,
            policy=dpg.mvTable_SizingFixedFit,
            height=-1,
        ):
            for col in columns:
                dpg.add_table_column(label=col, init_width_or_weight=min(max(len(str(col)) * 9, 80), 160))

            for i, row in enumerate(df.itertuples(index=False)):
                with dpg.table_row():
                    for value in row:
                        dpg.add_text(str(value) if value != 0 else "")
                dpg.highlight_table_row(_TABLE_TAG, i, _ROW_EVEN if i % 2 == 0 else _ROW_ODD)

    def show_stats(self, session_stats: db_handler.Stats, total_stats: db_handler.Stats | None = None) -> None:
        """Show session stats, with all-time totals in parentheses when they differ."""
        s, t = session_stats, total_stats

        if s.total_chests == 0:
            self._set_text("total_chests", "0" + (f" ({t.total_chests})" if t else ""))
            if t and t.total_chests > 0:
                self._set_text("rev_per_chest", f"N/A ({fmt_number(t.avg_revenue_per_chest)})")
                self._set_text("total_rev", f"N/A ({fmt_number(t.total_revenue)})")
            else:
                self._set_text("rev_per_chest", "N/A")
                self._set_text("total_rev", "N/A")
            return

        chests = str(s.total_chests)
        avg = fmt_number(s.avg_revenue_per_chest)
        revenue = fmt_number(s.total_revenue)
        if t and t.total_chests != s.total_chests:
            chests += f" ({t.total_chests})"
            avg += f" ({fmt_number(t.avg_revenue_per_chest)})"
            revenue += f" ({fmt_number(t.total_revenue)})"

        self._set_text("total_chests", chests)
        self._set_text("rev_per_chest", avg)
        self._set_text("total_rev", revenue)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_combo(self, sender, app_data) -> None:
        self._selected_chest = app_data
        self._on_chest_selected(app_data)

    def _on_checkbox(self, sender, app_data) -> None:
        self._session_mode = app_data
        self._on_session_toggle(app_data)

    def _set_text(self, key: str, text: str) -> None:
        tag = self._ids.get(key)
        if tag and dpg.does_item_exist(tag):
            dpg.configure_item(tag, default_value=text)

    @staticmethod
    def _sort_columns(columns: list[str], item_prices: dict[str, float]) -> list[str]:
        """Pinned columns first, then the rest by descending item price."""
        pinned = [c for c in PINNED_COLUMNS if c in columns]
        rest = sorted((c for c in columns if c not in pinned), key=lambda c: -item_prices.get(c.lower(), 0.0))
        return pinned + rest
