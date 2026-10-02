"""
Mini HUD: shrinks the viewport to a narrow, undecorated, always-on-top strip.
Opening saves the viewport size; closing restores it and shows the main window.
"""

from __future__ import annotations

from typing import Callable

import dearpygui.dearpygui as dpg

import config
from utils import fmt_number

_HUD_W = 480
_HUD_H = 36
_HUD_TAG = "mini_hud_content"

_BG = (20, 20, 24, 255)
_GRAY = (149, 165, 166, 255)
_GREEN = (46, 204, 113, 255)
_LABEL = (110, 120, 120, 255)


class MiniWindow:
    def __init__(self, on_close: Callable[[], None]) -> None:
        self._on_close = on_close
        self._alive = False

        self._saved_w = dpg.get_viewport_width()
        self._saved_h = dpg.get_viewport_height()

        self._dot_id: int | str = 0
        self._status_id: int | str = 0
        self._item_id: int | str = 0
        self._rev_id: int | str = 0

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        if dpg.does_item_exist(_HUD_TAG):
            dpg.delete_item(_HUD_TAG)

        dpg.set_viewport_decorated(False)
        dpg.set_viewport_min_width(100)
        dpg.set_viewport_min_height(_HUD_H)
        dpg.set_viewport_width(_HUD_W)
        dpg.set_viewport_height(_HUD_H)
        dpg.set_viewport_always_top(True)
        self._restore_saved_position()

        with dpg.window(
            tag=_HUD_TAG,
            no_title_bar=True,
            no_resize=True,
            no_collapse=True,
            no_close=True,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
            no_move=True,  # the viewport itself is dragged
            width=_HUD_W,
            height=_HUD_H,
            pos=[0, 0],
        ):
            self._bind_window_theme()

            with dpg.group(horizontal=True):
                self._dot_id = dpg.add_text("*", color=_GRAY)
                dpg.add_spacer(width=2)
                self._status_id = dpg.add_text("READY", color=_GRAY)
                dpg.add_text("  |", color=(55, 55, 60, 255))
                dpg.add_spacer(width=6)
                dpg.add_text("TOP:", color=_LABEL)
                dpg.add_spacer(width=4)
                self._item_id = dpg.add_text("-", color=(243, 156, 18, 255))
                dpg.add_spacer(width=10)
                dpg.add_text("AVG:", color=_LABEL)
                dpg.add_spacer(width=4)
                self._rev_id = dpg.add_text("N/A", color=_GREEN)
                dpg.add_spacer(width=8)
                self._add_close_button()

        dpg.hide_item("primary_window")
        self._alive = True

    @staticmethod
    def _restore_saved_position() -> None:
        try:
            x, y = config.load("mini_x"), config.load("mini_y")
            if x and y:
                dpg.set_viewport_pos([int(x), int(y)])
        except (ValueError, TypeError):
            pass

    @staticmethod
    def _bind_window_theme() -> None:
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvWindowAppItem):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, _BG)
                dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 40, 45, 255))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 8, 6)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 0)
        dpg.bind_item_theme(_HUD_TAG, theme)

    def _add_close_button(self) -> None:
        button = dpg.add_button(label="X", width=20, height=20, callback=self._on_close_btn)
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (90, 35, 35, 220))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (180, 55, 55, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (210, 40, 40, 255))
        dpg.bind_item_theme(button, theme)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        return self._alive

    def update(self, is_running: bool, most_expensive: tuple[str, float], avg_revenue: float) -> None:
        if not self._alive:
            return
        try:
            colour = _GREEN if is_running else _GRAY
            self._configure(self._dot_id, color=colour)
            self._configure(self._status_id, default_value="LIVE" if is_running else "READY", color=colour)

            item_name, item_value = most_expensive
            if item_value > 0:
                item_text = item_name[:24] + "..." if len(item_name) > 27 else item_name
            else:
                item_text = "-"
            self._configure(self._item_id, default_value=item_text)
            self._configure(self._rev_id, default_value=fmt_number(avg_revenue) if avg_revenue > 0 else "N/A")

            pos = dpg.get_viewport_pos()
            if pos:
                config.save({"mini_x": str(pos[0]), "mini_y": str(pos[1])})
        except Exception as exc:
            print(f"[mini_window] update error: {exc}")

    def close(self) -> None:
        self._alive = False
        if dpg.does_item_exist(_HUD_TAG):
            dpg.delete_item(_HUD_TAG)
        self._restore_viewport()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _configure(tag: int | str, **kwargs) -> None:
        if tag and dpg.does_item_exist(tag):
            dpg.configure_item(tag, **kwargs)

    def _on_close_btn(self, sender, app_data, user_data) -> None:
        self.close()
        self._on_close()

    def _restore_viewport(self) -> None:
        dpg.set_viewport_always_top(False)
        dpg.set_viewport_decorated(True)
        dpg.set_viewport_min_width(800)
        dpg.set_viewport_min_height(600)
        dpg.set_viewport_width(self._saved_w)
        dpg.set_viewport_height(self._saved_h)
        dpg.show_item("primary_window")
