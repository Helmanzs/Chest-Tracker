"""
ui/mini_window.py  (Dear PyGui port)
--------------------------------------
Mini HUD: shrinks the DPG viewport to a narrow strip, removes the OS title
bar decoration, and pins the window always-on-top.

On open:
  - saves current viewport size, position, decoration state
  - removes title bar (set_viewport_decorated(False))
  - resizes to HUD dimensions
  - sets always-on-top

On close:
  - restores all saved state
  - shows primary_window again
"""

from __future__ import annotations

from typing import Callable
import dearpygui.dearpygui as dpg

import config

# HUD dimensions
_HUD_W = 480
_HUD_H = 36  # just the content row -- no title bar

_BG_COL = (20, 20, 24, 255)


class MiniWindow:
    def __init__(self, on_close: Callable[[], None]) -> None:
        self._on_close = on_close
        self._alive = False
        self._hud_tag = "mini_hud_content"

        # Save everything we will change
        self._saved_w = dpg.get_viewport_width()
        self._saved_h = dpg.get_viewport_height()
        self._saved_decorated = dpg.is_viewport_decorated()

        self._dot_id: int | str = 0
        self._status_id: int | str = 0
        self._item_id: int | str = 0
        self._rev_id: int | str = 0

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        if dpg.does_item_exist(self._hud_tag):
            dpg.delete_item(self._hud_tag)

        # ── Shrink OS window ────────────────────────────────────────
        # Remove title bar so the strip has no wasted chrome
        dpg.set_viewport_decorated(False)
        # Allow the viewport to be as small as the HUD
        dpg.set_viewport_min_width(100)
        dpg.set_viewport_min_height(_HUD_H)
        dpg.set_viewport_width(_HUD_W)
        dpg.set_viewport_height(_HUD_H)
        dpg.set_viewport_always_top(True)

        # Restore saved HUD position if any
        try:
            raw_x = config.load("mini_x")
            raw_y = config.load("mini_y")
            if raw_x and raw_y:
                dpg.set_viewport_pos([int(raw_x), int(raw_y)])
        except (ValueError, TypeError):
            pass

        # ── HUD window (fills the now-tiny viewport exactly) ────────
        with dpg.window(
            tag=self._hud_tag,
            no_title_bar=True,
            no_resize=True,
            no_collapse=True,
            no_close=True,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
            no_move=True,  # viewport drag handles movement
            no_background=False,
            width=_HUD_W,
            height=_HUD_H,
            pos=[0, 0],
        ):
            with dpg.theme() as _th:
                with dpg.theme_component(dpg.mvWindowAppItem):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, _BG_COL)
                    dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 40, 45, 255))
                    dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 8, 6)
                    dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 0)
            dpg.bind_item_theme(self._hud_tag, _th)

            with dpg.group(horizontal=True):
                self._dot_id = dpg.add_text("*", color=(149, 165, 166, 255))
                dpg.add_spacer(width=2)
                self._status_id = dpg.add_text("READY", color=(149, 165, 166, 255))
                dpg.add_text("  |", color=(55, 55, 60, 255))
                dpg.add_spacer(width=6)
                dpg.add_text("TOP:", color=(110, 120, 120, 255))
                dpg.add_spacer(width=4)
                self._item_id = dpg.add_text("-", color=(243, 156, 18, 255))
                dpg.add_spacer(width=10)
                dpg.add_text("AVG:", color=(110, 120, 120, 255))
                dpg.add_spacer(width=4)
                self._rev_id = dpg.add_text("N/A", color=(46, 204, 113, 255))
                dpg.add_spacer(width=8)
                close_btn = dpg.add_button(
                    label="X",
                    width=20,
                    height=20,
                    user_data=None,
                    callback=self._on_close_btn,
                )
                with dpg.theme() as _cb:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(dpg.mvThemeCol_Button, (90, 35, 35, 220))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (180, 55, 55, 255))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (210, 40, 40, 255))
                dpg.bind_item_theme(close_btn, _cb)

        # Hide the main tab UI so only the HUD shows
        dpg.hide_item("primary_window")
        self._alive = True

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(
        self,
        is_running: bool,
        most_expensive: tuple[str, float],
        avg_revenue: float,
    ) -> None:
        if not self._alive:
            return
        try:
            colour = (46, 204, 113, 255) if is_running else (149, 165, 166, 255)
            label = "LIVE" if is_running else "READY"
            if dpg.does_item_exist(self._dot_id):
                dpg.configure_item(self._dot_id, color=colour)
            if dpg.does_item_exist(self._status_id):
                dpg.configure_item(self._status_id, default_value=label, color=colour)

            item_name, item_value = most_expensive
            if dpg.does_item_exist(self._item_id):
                if item_value > 0:
                    display = (item_name[:24] + "...") if len(item_name) > 27 else item_name
                    dpg.configure_item(self._item_id, default_value=display)
                else:
                    dpg.configure_item(self._item_id, default_value="-")

            if dpg.does_item_exist(self._rev_id):
                text = f"{avg_revenue:,.0f}".replace(",", " ") if avg_revenue > 0 else "N/A"
                dpg.configure_item(self._rev_id, default_value=text)

            # Persist HUD position
            pos = dpg.get_viewport_pos()
            if pos:
                config.save({"mini_x": str(pos[0]), "mini_y": str(pos[1])})

        except Exception as exc:
            print(f"[mini_window] update error: {exc}")

    def close(self) -> None:
        self._alive = False
        if dpg.does_item_exist(self._hud_tag):
            dpg.delete_item(self._hud_tag)
        self._restore()

    def is_alive(self) -> bool:
        return self._alive

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_close_btn(self, sender: int, app_data: object, user_data: object) -> None:
        self.close()
        self._on_close()

    def _restore(self) -> None:
        """Restore viewport to pre-mini state."""
        dpg.set_viewport_always_top(False)
        dpg.set_viewport_decorated(self._saved_decorated)
        dpg.set_viewport_min_width(800)
        dpg.set_viewport_min_height(600)
        dpg.set_viewport_width(self._saved_w)
        dpg.set_viewport_height(self._saved_h)
        dpg.show_item("primary_window")
