"""The "Live Tracker" tab: file config, start/stop, manual trigger and log."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

import dearpygui.dearpygui as dpg

from constants import BOUNTY_TIER_GROUPS, PRICE_TIER_HIGH, PRICE_TIER_MID, bounty_group_key
from ui.dialogs import show_modal
from ui.theme import GREEN, RED, button_theme

_COLOURS: dict[str, tuple[int, int, int, int]] = {
    "black": (220, 220, 220, 255),  # near-white on the dark background
    "blue": (100, 160, 255, 255),
    "green": (80, 200, 100, 255),
    "red": (255, 80, 80, 255),
    "orange": (255, 165, 40, 255),
    "gray": (160, 160, 160, 255),
    "light_gray": (120, 120, 120, 255),
    "dark_red": (200, 60, 60, 255),
    "purple": (180, 100, 255, 255),
}

_MAX_LOG_LINES = 500
_FILE_DIALOG_TAG = "tracker_file_dialog"

_HINT_NEUTRAL = "← select before the next bounty drops"
_HINT_DETECTED = "← bounty detected! confirm tier before it saves"


class TrackerTab:
    def __init__(
        self,
        parent_tag: str | int,
        on_start_stop: Callable[[], None],
        on_manual: Callable[[str], None],
        on_log_browse: Callable[[str], None],
        initial_log_path: str = "",
    ) -> None:
        self._parent = parent_tag
        self._on_start_stop = on_start_stop
        self._on_manual = on_manual
        self._on_log_browse = on_log_browse

        self._item_prices: dict[str, float] = {}
        self._ids: dict[str, int | str] = {}
        self._button_themes: dict[int | str, int] = {}  # button tag -> theme id

        self._build(initial_log_path)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self, log_path: str) -> None:
        with dpg.group(parent=self._parent):
            self._build_file_config(log_path)
            dpg.add_spacer(height=4)
            self._build_bounty_override()
            dpg.add_spacer(height=4)

            self._ids["status"] = dpg.add_text("Status: Ready", color=_COLOURS["gray"])
            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                self._ids["btn_toggle"] = dpg.add_button(
                    label="START LISTENING", width=200, height=36, callback=self._on_start_stop
                )
                self._set_button_colour(self._ids["btn_toggle"], GREEN)

                dpg.add_button(label="MANUAL CHEST", width=180, height=36, callback=self._manual_btn_pressed)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            self._ids["log_display"] = dpg.add_child_window(
                tag="tracker_log_display", width=-1, height=-1, border=True
            )

    def _build_file_config(self, log_path: str) -> None:
        with dpg.collapsing_header(label="File Configuration", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_text("Log file:")
                self._ids["log_label"] = dpg.add_text(self._short(log_path), color=_COLOURS["blue"])
                dpg.add_button(label="Browse", callback=self._browse_log)

            with dpg.group(horizontal=True):
                dpg.add_text("Active chest:")
                self._ids["sheet_label"] = dpg.add_text("Auto-detect from log", color=_COLOURS["purple"])

            with dpg.group(horizontal=True):
                dpg.add_text("Manual chest:")
                self._ids["manual_combo"] = dpg.add_combo(items=[], label="", width=300, tag="tracker_manual_combo")

    def _build_bounty_override(self) -> None:
        # Every tier from every group is selectable, whichever bounty was last detected.
        all_tiers = [tier for tiers in BOUNTY_TIER_GROUPS.values() for tier in tiers]

        with dpg.collapsing_header(label="Bounty Tier Override", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_text("Override tier:", indent=8)
                self._ids["bounty_override_combo"] = dpg.add_combo(
                    items=all_tiers,
                    default_value=all_tiers[0] if all_tiers else "",
                    width=320,
                    label="",
                    tag="tracker_bounty_override_combo",
                )
                dpg.add_spacer(width=8)
                self._ids["bounty_override_hint"] = dpg.add_text(_HINT_NEUTRAL, color=_COLOURS["gray"])

            dpg.add_spacer(height=2)
            dpg.add_text(
                "When a bounty chest is auto-detected, loot will be recorded\n"
                "under the tier selected above instead of the base type.",
                color=(130, 130, 130, 255),
                indent=8,
            )

    # ------------------------------------------------------------------
    # Log & status
    # ------------------------------------------------------------------

    def log(self, message: str, colour: str = "black") -> None:
        """Append a timestamped, coloured line to the log and scroll to the bottom."""
        window = self._ids["log_display"]
        if not dpg.does_item_exist(window):
            return

        line = f"[{datetime.now():%H:%M:%S}] {message}"
        dpg.add_text(line, color=_COLOURS.get(colour, _COLOURS["black"]), parent=window)

        children: list[int] = dpg.get_item_children(window, 1) or []  # type: ignore[assignment]
        for old in children[: max(0, len(children) - _MAX_LOG_LINES)]:
            dpg.delete_item(old)

        # Scroll one frame later, once DPG has laid out the new line.
        dpg.set_frame_callback(
            dpg.get_frame_count() + 1,
            callback=lambda: (
                dpg.set_y_scroll(window, dpg.get_y_scroll_max(window)) if dpg.does_item_exist(window) else None
            ),
        )

    def set_status(self, text: str, colour: str = "gray") -> None:
        self._configure("status", default_value=f"Status: {text}", color=_COLOURS.get(colour, _COLOURS["gray"]))

    def set_listening(self, listening: bool) -> None:
        self._set_toggle("btn_toggle", listening, "STOP LISTENING", "START LISTENING", RED, GREEN)

    def set_sheet_label(self, name: str) -> None:
        self._configure("sheet_label", default_value=name or "Auto")

    def set_log_path_label(self, path: str) -> None:
        self._configure("log_label", default_value=self._short(path))

    def set_chest_types(self, chest_types: list[str]) -> None:
        combo = self._ids["manual_combo"]
        if not dpg.does_item_exist(combo):
            return
        current = dpg.get_value(combo)
        dpg.configure_item(combo, items=chest_types)
        if chest_types and not current:
            dpg.set_value(combo, chest_types[0])

    def set_item_prices(self, prices: dict[str, float]) -> None:
        self._item_prices = prices

    def get_item_colour(self, item_name: str) -> str:
        """Log colour for an item by price tier (prices are keyed by lower-case name)."""
        if not self._item_prices:
            return "black"
        price = self._item_prices.get(item_name.strip().lower(), 0)
        if price == 0:
            return "light_gray"
        if price >= PRICE_TIER_HIGH:
            return "dark_red"
        if price >= PRICE_TIER_MID:
            return "black"
        return "gray"

    # ------------------------------------------------------------------
    # Bounty override
    # ------------------------------------------------------------------

    def get_bounty_override(self) -> str:
        combo = self._ids["bounty_override_combo"]
        return dpg.get_value(combo) if dpg.does_item_exist(combo) else ""

    def update_bounty_override_options(self, detected_chest: str) -> None:
        """
        A bounty chest was detected: pre-select a tier from its group (unless the
        current selection already belongs to it) and highlight the hint.
        """
        group_key = bounty_group_key(detected_chest)
        if group_key is None:
            return

        tiers = BOUNTY_TIER_GROUPS.get(group_key, [])
        combo = self._ids["bounty_override_combo"]
        if dpg.does_item_exist(combo) and tiers and dpg.get_value(combo) not in tiers:
            dpg.set_value(combo, tiers[0])

        self._configure("bounty_override_hint", default_value=_HINT_DETECTED, color=_COLOURS["orange"])

    def reset_bounty_override_hint(self) -> None:
        self._configure("bounty_override_hint", default_value=_HINT_NEUTRAL, color=_COLOURS["gray"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _configure(self, key: str, **kwargs) -> None:
        tag = self._ids.get(key)
        if tag and dpg.does_item_exist(tag):
            dpg.configure_item(tag, **kwargs)

    def _set_toggle(
        self,
        key: str,
        active: bool,
        active_label: str,
        idle_label: str,
        active_rgb: tuple[int, int, int],
        idle_rgb: tuple[int, int, int],
    ) -> None:
        button = self._ids[key]
        if not dpg.does_item_exist(button):
            return
        dpg.configure_item(button, label=active_label if active else idle_label)
        self._set_button_colour(button, active_rgb if active else idle_rgb)

    def _set_button_colour(self, tag: int | str, rgb: tuple[int, int, int]) -> None:
        """Bind a colour theme to *tag*, deleting the one it replaces."""
        previous = self._button_themes.get(tag)
        if previous is not None and dpg.does_item_exist(previous):
            dpg.delete_item(previous)
        theme = button_theme(rgb)
        self._button_themes[tag] = theme
        dpg.bind_item_theme(tag, theme)

    @staticmethod
    def _short(path: str) -> str:
        return os.path.basename(path) if path else "Not selected"

    def _manual_btn_pressed(self) -> None:
        selected = dpg.get_value(self._ids["manual_combo"])
        if not selected:
            show_modal("No Chest Selected", "Select a chest type in the Manual chest dropdown first.")
            return
        self._on_manual(selected)

    def _browse_log(self) -> None:
        def close_dialog() -> None:
            if dpg.does_item_exist(_FILE_DIALOG_TAG):
                dpg.delete_item(_FILE_DIALOG_TAG)

        def on_selected(sender, app_data) -> None:
            selections = app_data.get("selections", {})
            if selections:
                path = next(iter(selections.values()))
                self.set_log_path_label(path)
                self._on_log_browse(path)
            close_dialog()

        close_dialog()
        dpg.add_file_dialog(
            tag=_FILE_DIALOG_TAG,
            label="Select Log File",
            width=700,
            height=450,
            modal=True,
            callback=on_selected,
            cancel_callback=lambda s, a: close_dialog(),
        )
        for ext in (".log", ".txt", ".*"):
            dpg.add_file_extension(ext, parent=_FILE_DIALOG_TAG)
