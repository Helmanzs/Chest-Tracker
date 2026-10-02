"""Shared theme helpers."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

GREEN = (46, 204, 113)
RED = (231, 76, 60)


def button_theme(rgb: tuple[int, int, int]) -> int:
    """Create a theme colouring a button *rgb* (slightly translucent at rest, darker when pressed)."""
    r, g, b = rgb
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 220))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (max(0, r - 30), max(0, g - 30), max(0, b - 30), 255))
    return theme


def color_button(tag: int | str, rgb: tuple[int, int, int]) -> None:
    """Colour an existing button."""
    dpg.bind_item_theme(tag, button_theme(rgb))
