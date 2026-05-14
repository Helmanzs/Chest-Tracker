"""
ui/viewer_tab.py  (Dear PyGui port)
-------------------------------------
The "Excel Data" tab: chest selector, session toggle, export,
statistics panel, and a scrollable data table.
"""

from __future__ import annotations

from typing import Callable
import dearpygui.dearpygui as dpg
import pandas as pd

import db_handler

PINNED_COLUMNS = ["#", "chest_id", "recorded_at", "Shard", "Energy Fragment"]

# Table row alternating colours (RGBA)
_ROW_EVEN = (50, 50, 55, 255)
_ROW_ODD = (42, 42, 48, 255)


class ViewerTab:
    """Manages all widgets inside the Excel Data tab."""

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

        default = initial_chest if initial_chest in chest_types else (chest_types[0] if chest_types else "")
        self._selected_chest = default
        self._session_mode = False

        self._ids: dict[str, int | str] = {}
        self._current_columns: list[str] = []
        self._table_row_count = 0

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        with dpg.group(parent=self._parent):
            # -- Top row ---------------------------------------------
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
                self._ids["session_cb"] = dpg.add_checkbox(
                    label="Show current session only",
                    default_value=False,
                    callback=self._on_checkbox,
                )

            dpg.add_spacer(height=4)

            # -- Button row ------------------------------------------
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh Data", callback=self._on_refresh)
                dpg.add_spacer(width=4)
                dpg.add_button(label="Reload Prices", callback=self._on_reload_prices)
                dpg.add_spacer(width=4)
                btn_export = dpg.add_button(
                    label="Export to Excel",
                    callback=self._on_export,
                )
                # Green theme for export button
                with dpg.theme() as exp_theme:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(dpg.mvThemeCol_Button, (39, 174, 96, 220))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (39, 174, 96, 255))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (27, 130, 70, 255))
                dpg.bind_item_theme(btn_export, exp_theme)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # -- Statistics ------------------------------------------
            with dpg.collapsing_header(label="Statistics", default_open=True):
                with dpg.table(header_row=False, borders_innerV=False, borders_outerV=False):
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=150)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=180)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=150)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=220)

                    with dpg.table_row():
                        dpg.add_text("Chests:")
                        self._ids["total_chests"] = dpg.add_text("0", color=(100, 140, 255, 255))
                        dpg.add_text("Revenue/Chest:")
                        self._ids["rev_per_chest"] = dpg.add_text("N/A", color=(80, 200, 100, 255))

                    with dpg.table_row():
                        dpg.add_text("Total Revenue:")
                        self._ids["total_rev"] = dpg.add_text("N/A", color=(80, 200, 100, 255))

            dpg.add_spacer(height=6)

            # -- Data table (scrollable child window) ----------------
            self._ids["table_container"] = dpg.add_child_window(
                width=-1,
                height=-1,
                horizontal_scrollbar=True,
                border=True,
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def selected_chest(self) -> str:
        return self._selected_chest

    def set_selected_chest(self, chest_type: str) -> None:
        if chest_type in self._chest_types:
            self._selected_chest = chest_type
            if dpg.does_item_exist(self._ids["chest_combo"]):
                dpg.set_value(self._ids["chest_combo"], chest_type)

    def set_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        if dpg.does_item_exist(self._ids["chest_combo"]):
            dpg.configure_item(self._ids["chest_combo"], items=chest_types)
            if chest_types and not self._selected_chest:
                self._selected_chest = chest_types[0]
                dpg.set_value(self._ids["chest_combo"], chest_types[0])

    def load_dataframe(
        self,
        df: pd.DataFrame,
        item_prices: dict[str, float] | None = None,
    ) -> None:
        container = self._ids.get("table_container")
        if not container or not dpg.does_item_exist(container):
            return

        # Clear existing table
        if dpg.does_item_exist("viewer_data_table"):
            dpg.delete_item("viewer_data_table")
        self._current_columns = []
        self._table_row_count = 0

        if df.empty:
            with dpg.group(parent=container, tag="_viewer_empty_msg"):
                dpg.add_spacer(height=10)
                dpg.add_text("No data yet -- start tracking chests!", color=(160, 160, 160, 255))
            return

        # Remove stale empty msg
        if dpg.does_item_exist("_viewer_empty_msg"):
            dpg.delete_item("_viewer_empty_msg")

        cols = self._sort_columns(list(df.columns), item_prices or {})
        df = df[cols]
        self._current_columns = cols

        with dpg.table(
            tag="viewer_data_table",
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
            for col in cols:
                w = max(len(str(col)) * 9, 80)
                dpg.add_table_column(
                    label=col,
                    init_width_or_weight=min(w, 160),
                )

            for i, (_, row) in enumerate(df.iterrows()):
                with dpg.table_row():
                    for val in row:
                        dpg.add_text(str(val) if val != 0 else "")
                # Alternating row colour
                colour = _ROW_EVEN if i % 2 == 0 else _ROW_ODD
                dpg.highlight_table_row("viewer_data_table", i, colour)

            self._table_row_count = len(df)

    def show_stats(
        self,
        session_stats: db_handler.Stats,
        total_stats: db_handler.Stats | None = None,
    ) -> None:
        s, t = session_stats, total_stats

        if s.total_chests == 0:
            chests_text = "0" + (f" ({t.total_chests})" if t else "")
            self._set_text("total_chests", chests_text)
            if t and t.total_chests > 0:
                self._set_text("rev_per_chest", f"N/A ({self._fmt(t.avg_revenue_per_chest)})")
                self._set_text("total_rev", f"N/A ({self._fmt(t.total_revenue)})")
            else:
                self._set_text("rev_per_chest", "N/A")
                self._set_text("total_rev", "N/A")
            return

        chests_text = str(s.total_chests)
        if t and t.total_chests != s.total_chests:
            chests_text += f" ({t.total_chests})"
        self._set_text("total_chests", chests_text)

        avg_text = self._fmt(s.avg_revenue_per_chest)
        if t and t.total_chests != s.total_chests:
            avg_text += f" ({self._fmt(t.avg_revenue_per_chest)})"
        self._set_text("rev_per_chest", avg_text)

        total_text = self._fmt(s.total_revenue)
        if t and t.total_chests != s.total_chests:
            total_text += f" ({self._fmt(t.total_revenue)})"
        self._set_text("total_rev", total_text)

    def show_stats_error(self) -> None:
        for key in ("total_chests", "rev_per_chest", "total_rev"):
            self._set_text(key, "Error")

    def is_session_mode(self) -> bool:
        return self._session_mode

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
    def _sort_columns(cols: list[str], item_prices: dict[str, float]) -> list[str]:
        pinned = [c for c in PINNED_COLUMNS if c in cols]
        unpinned = [c for c in cols if c not in pinned]
        unpinned.sort(key=lambda c: -item_prices.get(c.lower(), 0.0))
        return pinned + unpinned

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{int(value):,}".replace(",", " ")
