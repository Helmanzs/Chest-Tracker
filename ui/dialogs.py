"""Shared dialog helpers."""

from __future__ import annotations

import itertools

import dearpygui.dearpygui as dpg

_modal_ids = itertools.count(1)


def show_modal(title: str, message: str) -> None:
    """Show a simple modal with an OK button."""
    tag = f"_modal_{next(_modal_ids)}"

    def close() -> None:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

    with dpg.window(
        label=title,
        modal=True,
        tag=tag,
        no_resize=True,
        width=420,
        min_size=(320, 120),
        pos=(300, 250),
        on_close=close,
    ):
        dpg.add_text(message, wrap=400)
        dpg.add_spacer(height=8)
        dpg.add_button(label="OK", width=80, callback=close)


def centered_pos(width: int, height: int) -> list[int]:
    """Top-left position that centres a *width* x *height* window in the viewport."""
    return [
        max(0, (dpg.get_viewport_width() - width) // 2),
        max(0, (dpg.get_viewport_height() - height) // 2),
    ]
