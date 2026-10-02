"""Modal dialog shown on first launch (or failed connection) to collect the Supabase access key."""

from __future__ import annotations

import threading
from typing import Callable

import dearpygui.dearpygui as dpg

import config
import db_handler
from ui.dialogs import centered_pos
from ui.theme import GREEN, color_button

_DIALOG_TAG = "setup_dialog_win"
_WIDTH = 480
_ERROR = (255, 80, 80, 255)


class SetupDialog:
    """
    on_success(url, key) : called once the key has been validated and saved
    on_cancel()          : called if the user closes the dialog without connecting
    existing_key         : pre-fills the key field
    """

    def __init__(
        self,
        on_success: Callable[[str, str], None],
        on_cancel: Callable[[], None],
        existing_key: str = "",
    ) -> None:
        self._on_success = on_success
        self._on_cancel = on_cancel
        self._existing_key = existing_key
        self._ids: dict[str, int | str] = {}
        self._build()

    def _build(self) -> None:
        if dpg.does_item_exist(_DIALOG_TAG):
            dpg.delete_item(_DIALOG_TAG)

        with dpg.window(
            tag=_DIALOG_TAG,
            label="Chest Tracker — Setup",
            modal=True,
            no_resize=True,
            no_close=True,
            width=_WIDTH,
            pos=centered_pos(_WIDTH, 300),
        ):
            dpg.add_spacer(height=10)
            dpg.add_text("Welcome to Chest Tracker", color=(255, 255, 255, 255))
            dpg.add_spacer(height=4)
            dpg.add_text(
                "Please enter your access key to connect to the database.",
                color=(180, 180, 180, 255),
                wrap=460,
            )
            dpg.add_spacer(height=12)

            with dpg.group(horizontal=True):
                dpg.add_text("Access Key:", indent=8)
                self._ids["key_input"] = dpg.add_input_text(
                    default_value=self._existing_key,
                    width=300,
                    label="",
                    hint="Paste your Supabase key here",
                    password=True,
                )

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=100)
                dpg.add_checkbox(label="Show key", default_value=False, callback=self._toggle_show)

            dpg.add_spacer(height=8)
            self._ids["status"] = dpg.add_text("", color=_ERROR, wrap=460, indent=8)
            dpg.add_spacer(height=8)

            with dpg.group(horizontal=True):
                dpg.add_spacer(width=100)
                self._ids["connect_btn"] = dpg.add_button(
                    label="Connect", width=100, height=32, callback=self._try_connect
                )
                color_button(self._ids["connect_btn"], GREEN)
                dpg.add_spacer(width=8)
                dpg.add_button(label="Cancel", width=80, height=32, callback=self._cancel)

            dpg.add_spacer(height=10)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _toggle_show(self, sender, app_data) -> None:
        dpg.configure_item(self._ids["key_input"], password=not app_data)

    def _set_status(self, text: str, colour: tuple[int, int, int, int] = _ERROR) -> None:
        tag = self._ids["status"]
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, default_value=text, color=colour)

    def _try_connect(self) -> None:
        key = dpg.get_value(self._ids["key_input"]).strip()
        if not key:
            self._set_status("Please enter an access key.")
            return

        self._set_status("Connecting…", (200, 200, 80, 255))
        dpg.configure_item(self._ids["connect_btn"], enabled=False)
        threading.Thread(target=self._connect_worker, args=(key,), daemon=True).start()

    def _connect_worker(self, key: str) -> None:
        url = config.DEFAULT_SUPABASE_URL
        if db_handler.init(url, key):
            config.save_supabase(url, key)
            dpg.split_frame()
            if dpg.does_item_exist(_DIALOG_TAG):
                dpg.delete_item(_DIALOG_TAG)
            self._on_success(url, key)
            return

        self._set_status("Invalid key or connection failed. Please check and try again.")
        button = self._ids["connect_btn"]
        if dpg.does_item_exist(button):
            dpg.configure_item(button, enabled=True)

    def _cancel(self) -> None:
        if dpg.does_item_exist(_DIALOG_TAG):
            dpg.delete_item(_DIALOG_TAG)
        self._on_cancel()
