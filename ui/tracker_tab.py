"""
ui/tracker_tab.py  (Dear PyGui port)
-------------------------------------
The "Live Tracker" tab: file configuration, start/stop controls,
manual trigger, mini-mode toggle, and the scrolled log display.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable
import dearpygui.dearpygui as dpg

from constants import PRICE_TIER_HIGH, PRICE_TIER_MID, BOUNTY_TIER_GROUPS

LogCallback = Callable[[str, str], None]

# Colour map: tag name -> RGBA tuple (0-255)
_COLOURS: dict[str, tuple[int, int, int, int]] = {
    "black": (220, 220, 220, 255),  # near-white on dark bg
    "blue": (100, 160, 255, 255),
    "green": (80, 200, 100, 255),
    "red": (255, 80, 80, 255),
    "orange": (255, 165, 40, 255),
    "gray": (160, 160, 160, 255),
    "light_gray": (120, 120, 120, 255),
    "dark_red": (200, 60, 60, 255),
    "purple": (180, 100, 255, 255),
}

# Max lines kept in the log buffer
_MAX_LOG_LINES = 500

# Collect all bounty chest names that have tier siblings so we can build
# a flat set for quick "is this a bounty?" checks.
_ALL_BOUNTY_CHEST_NAMES: frozenset[str] = frozenset(tier for tiers in BOUNTY_TIER_GROUPS.values() for tier in tiers)


class TrackerTab:
    """Manages all widgets inside the Live Tracker tab."""

    def __init__(
        self,
        parent_tag: str | int,
        on_start_stop: Callable[[], None],
        on_manual: Callable[[str], None],
        on_mini_toggle: Callable[[], None],
        on_log_browse: Callable[[str], None],
        initial_log_path: str = "",
    ) -> None:
        self._parent = parent_tag
        self._on_start_stop = on_start_stop
        self._on_manual = on_manual
        self._on_mini_toggle = on_mini_toggle
        self._on_log_browse = on_log_browse

        self._item_prices: dict[str, float] = {}
        self._chest_types: list[str] = []

        # Log lines stored as (text, colour_key) for re-render
        self._log_lines: list[tuple[str, str]] = []

        self._ids: dict[str, int | str] = {}
        # Map str(button_tag) -> DPG theme integer ID for clean re-creation
        self._btn_themes: dict[str, int] = {}
        self._build(initial_log_path)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self, log_path: str) -> None:
        with dpg.group(parent=self._parent):
            # -- File config -----------------------------------------
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
                    self._ids["manual_combo"] = dpg.add_combo(
                        items=[], label="", width=300, tag="tracker_manual_combo"
                    )

            dpg.add_spacer(height=4)

            # -- Bounty tier override ---------------------------------
            # Shown always; dimmed with a note when no bounty is active.
            # The row collapses to a single collapsing_header so it
            # doesn't clutter the main layout.
            with dpg.collapsing_header(label="Bounty Tier Override", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Override tier:", indent=8)
                    # Populate with the first bounty group's tiers as a
                    # sensible default; updated dynamically when a bounty
                    # chest is detected.
                    _default_tiers = next(iter(BOUNTY_TIER_GROUPS.values()), [])
                    self._ids["bounty_override_combo"] = dpg.add_combo(
                        items=_default_tiers,
                        default_value=_default_tiers[0] if _default_tiers else "",
                        width=320,
                        label="",
                        tag="tracker_bounty_override_combo",
                    )
                    dpg.add_spacer(width=8)
                    self._ids["bounty_override_hint"] = dpg.add_text(
                        "← select before the next bounty drops",
                        color=_COLOURS["gray"],
                    )

                dpg.add_spacer(height=2)
                dpg.add_text(
                    "When a bounty chest is auto-detected, loot will be recorded\n"
                    "under the tier selected above instead of the base type.",
                    color=(130, 130, 130, 255),
                    indent=8,
                )

            dpg.add_spacer(height=4)

            # -- Status ----------------------------------------------
            self._ids["status"] = dpg.add_text("Status: Ready", color=_COLOURS["gray"])

            dpg.add_spacer(height=4)

            # -- Buttons ---------------------------------------------
            with dpg.group(horizontal=True):
                self._ids["btn_toggle"] = dpg.add_button(
                    label="START LISTENING",
                    width=200,
                    height=36,
                    callback=self._on_start_stop,
                )
                self._apply_button_theme(self._ids["btn_toggle"], (46, 204, 113))

                dpg.add_button(
                    label="MANUAL CHEST",
                    width=180,
                    height=36,
                    callback=self._manual_btn_pressed,
                )

                self._ids["btn_mini"] = dpg.add_button(
                    label="MINI MODE",
                    width=150,
                    height=36,
                    callback=self._on_mini_toggle,
                )
                self._apply_button_theme(self._ids["btn_mini"], (155, 89, 182))

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # -- Log display: child_window with one add_text per line --
            # This is the only DPG way to get per-line colour in a log.
            self._ids["log_display"] = dpg.add_child_window(
                tag="tracker_log_display",
                width=-1,
                height=-1,
                border=True,
                no_scrollbar=False,
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def log(self, message: str, colour: str = "black") -> None:
        """Append a timestamped, coloured line to the log display."""
        win = self._ids.get("log_display")
        if not win or not dpg.does_item_exist(win):
            return

        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        col = _COLOURS.get(colour, _COLOURS["black"])

        # Add the new text widget as a child of the log window
        dpg.add_text(line, color=col, parent=win)

        # Trim oldest lines when buffer exceeds max
        children: list[int] = dpg.get_item_children(win, 1) or []  # type: ignore[assignment]
        while len(children) > _MAX_LOG_LINES:
            dpg.delete_item(children[0])
            children = children[1:]

        # Defer scroll by one frame so DPG has laid out the new widget
        _win = win
        dpg.set_frame_callback(
            dpg.get_frame_count() + 1,
            callback=lambda: dpg.set_y_scroll(_win, dpg.get_y_scroll_max(_win)) if dpg.does_item_exist(_win) else None,
        )

    def set_status(self, text: str, colour: str = "gray") -> None:
        if dpg.does_item_exist(self._ids["status"]):
            dpg.configure_item(
                self._ids["status"],
                default_value=f"Status: {text}",
                color=_COLOURS.get(colour, _COLOURS["gray"]),
            )

    def set_listening(self, listening: bool) -> None:
        if not dpg.does_item_exist(self._ids["btn_toggle"]):
            return
        if listening:
            dpg.configure_item(self._ids["btn_toggle"], label="STOP LISTENING")
            self._apply_button_theme(self._ids["btn_toggle"], (231, 76, 60))
        else:
            dpg.configure_item(self._ids["btn_toggle"], label="START LISTENING")
            self._apply_button_theme(self._ids["btn_toggle"], (46, 204, 113))

    def set_mini_active(self, active: bool) -> None:
        if not dpg.does_item_exist(self._ids["btn_mini"]):
            return
        if active:
            dpg.configure_item(self._ids["btn_mini"], label="CLOSE MINI")
            self._apply_button_theme(self._ids["btn_mini"], (231, 76, 60))
        else:
            dpg.configure_item(self._ids["btn_mini"], label="MINI MODE")
            self._apply_button_theme(self._ids["btn_mini"], (155, 89, 182))

    def set_sheet_label(self, name: str) -> None:
        if dpg.does_item_exist(self._ids["sheet_label"]):
            dpg.configure_item(
                self._ids["sheet_label"],
                default_value=name or "Auto",
            )

    def set_log_path_label(self, path: str) -> None:
        if dpg.does_item_exist(self._ids["log_label"]):
            dpg.configure_item(
                self._ids["log_label"],
                default_value=self._short(path),
            )

    def set_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        tag = self._ids.get("manual_combo")
        if tag and dpg.does_item_exist(tag):
            current = dpg.get_value(tag)
            dpg.configure_item(tag, items=chest_types)
            if chest_types and not current:
                dpg.set_value(tag, chest_types[0])

    def set_item_prices(self, prices: dict[str, float]) -> None:
        self._item_prices = prices

    def get_item_colour(self, item_name: str) -> str:
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
    # Bounty override public API
    # ------------------------------------------------------------------

    def get_bounty_override(self) -> str:
        """
        Return the chest type name currently selected in the bounty override
        dropdown, or an empty string if the widget doesn't exist.
        """
        tag = self._ids.get("bounty_override_combo")
        if tag and dpg.does_item_exist(tag):
            return dpg.get_value(tag)
        return ""

    def update_bounty_override_options(self, detected_chest: str) -> None:
        """
        Called when a pattern-detected bounty chest is found.
        Updates the dropdown to show only the tiers relevant to that base
        chest and highlights the hint text so the user notices it.

        If the detected chest is not a bounty type, this is a no-op.
        """
        # Resolve to canonical group key (detected_chest might itself be a
        # tier name rather than the pattern key, e.g. "Portal Bounty Chest (Normal)")
        group_key = self._resolve_bounty_group_key(detected_chest)
        if group_key is None:
            return

        tiers = BOUNTY_TIER_GROUPS[group_key]
        tag = self._ids.get("bounty_override_combo")
        hint = self._ids.get("bounty_override_hint")

        if tag and dpg.does_item_exist(tag):
            current = dpg.get_value(tag)
            dpg.configure_item(tag, items=tiers)
            # Keep current selection if it's still valid, otherwise reset
            if current not in tiers:
                dpg.set_value(tag, tiers[0])

        if hint and dpg.does_item_exist(hint):
            dpg.configure_item(
                hint,
                default_value="← bounty detected! confirm tier before it saves",
                color=_COLOURS["orange"],
            )

    def reset_bounty_override_hint(self) -> None:
        """Restore the hint text to its neutral colour after a chest is saved."""
        hint = self._ids.get("bounty_override_hint")
        if hint and dpg.does_item_exist(hint):
            dpg.configure_item(
                hint,
                default_value="← select before the next bounty drops",
                color=_COLOURS["gray"],
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_bounty_group_key(chest_name: str) -> str | None:
        """
        Return the BOUNTY_TIER_GROUPS key that contains *chest_name*,
        or None if it is not part of any bounty group.
        """
        if chest_name in BOUNTY_TIER_GROUPS:
            return chest_name
        for key, tiers in BOUNTY_TIER_GROUPS.items():
            if chest_name in tiers:
                return key
        return None

    @staticmethod
    def _short(path: str) -> str:
        import os

        return os.path.basename(path) if path else "Not selected"

    def _manual_btn_pressed(self) -> None:
        tag = self._ids.get("manual_combo")
        if tag:
            selected = dpg.get_value(tag)
            if not selected:
                _show_modal(
                    "No Chest Selected",
                    "Select a chest type in the Manual chest dropdown first.",
                )
                return
            self._on_manual(selected)

    def _browse_log(self) -> None:
        def _file_selected(sender, app_data):
            selections = app_data.get("selections", {})
            if selections:
                path = list(selections.values())[0]
                if dpg.does_item_exist(self._ids["log_label"]):
                    dpg.configure_item(
                        self._ids["log_label"],
                        default_value=self._short(path),
                    )
                self._on_log_browse(path)
            if dpg.does_item_exist("tracker_file_dialog"):
                dpg.delete_item("tracker_file_dialog")

        if dpg.does_item_exist("tracker_file_dialog"):
            dpg.delete_item("tracker_file_dialog")

        dpg.add_file_dialog(
            tag="tracker_file_dialog",
            label="Select Log File",
            width=700,
            height=450,
            modal=True,
            callback=_file_selected,
            cancel_callback=lambda s, a: (
                dpg.delete_item("tracker_file_dialog") if dpg.does_item_exist("tracker_file_dialog") else None
            ),
        )
        dpg.add_file_extension(".log", parent="tracker_file_dialog")
        dpg.add_file_extension(".txt", parent="tracker_file_dialog")
        dpg.add_file_extension(".*", parent="tracker_file_dialog")

    def _apply_button_theme(self, tag: int | str, rgb: tuple[int, int, int]) -> None:
        """Create and bind a colour theme to a button (integer-ID, no alias)."""
        r, g, b = rgb
        key = str(tag)
        # Delete old theme if stored for this button
        prev = self._btn_themes.get(key)
        if prev is not None and dpg.does_item_exist(prev):
            dpg.delete_item(prev)
        theme_id: int = dpg.add_theme()  # type: ignore[assignment]
        with dpg.theme_component(dpg.mvButton, parent=theme_id):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 220))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (max(0, r - 30), max(0, g - 30), max(0, b - 30), 255))
        self._btn_themes[key] = theme_id
        dpg.bind_item_theme(tag, theme_id)


# ---------------------------------------------------------------------------
# Shared modal helper (module-level so other tabs can use it)
# ---------------------------------------------------------------------------

_modal_counter = 0


def _show_modal(title: str, message: str) -> None:
    global _modal_counter
    _modal_counter += 1
    tag = f"_modal_{_modal_counter}"

    with dpg.window(
        label=title,
        modal=True,
        tag=tag,
        no_resize=True,
        width=420,
        min_size=(320, 120),
        pos=(300, 250),
        no_close=False,
        on_close=lambda: dpg.delete_item(tag) if dpg.does_item_exist(tag) else None,
    ):
        dpg.add_text(message, wrap=400)
        dpg.add_spacer(height=8)
        dpg.add_button(
            label="OK",
            width=80,
            callback=lambda: dpg.delete_item(tag) if dpg.does_item_exist(tag) else None,
        )
