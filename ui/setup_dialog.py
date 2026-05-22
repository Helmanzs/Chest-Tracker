"""
ui/setup_dialog.py  (Dear PyGui port)
----------------------------------------
Modal dialog shown on first launch to gather the Supabase access key.
"""

from __future__ import annotations

from typing import Callable
import dearpygui.dearpygui as dpg

_DIALOG_TAG = "setup_dialog_win"
_SUPABASE_URL = "https://wwgczilevfjyivjmgoia.supabase.co"


class SetupDialog:
    """
    Modal dialog asking for the Supabase access key.

    Parameters
    ----------
    on_success : called with (url, key) when validated
    on_cancel  : called if user closes without a valid key
    existing_key : pre-fill the field if a key is already on disk
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

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        if dpg.does_item_exist(_DIALOG_TAG):
            dpg.delete_item(_DIALOG_TAG)

        vw = dpg.get_viewport_width()
        vh = dpg.get_viewport_height()
        x = max(0, (vw - 480) // 2)
        y = max(0, (vh - 300) // 2)

        with dpg.window(
            tag=_DIALOG_TAG,
            label="Chest Tracker — Setup",
            modal=True,
            no_resize=True,
            no_close=True,
            width=480,
            pos=[x, y],
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
                    callback=lambda s, a: None,
                )

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=100)
                self._ids["show_cb"] = dpg.add_checkbox(
                    label="Show key",
                    default_value=False,
                    callback=self._toggle_show,
                )

            dpg.add_spacer(height=8)
            self._ids["status"] = dpg.add_text("", color=(255, 80, 80, 255), wrap=460, indent=8)
            dpg.add_spacer(height=8)

            with dpg.group(horizontal=True):
                dpg.add_spacer(width=100)
                self._ids["connect_btn"] = dpg.add_button(
                    label="Connect",
                    width=100,
                    height=32,
                    callback=self._try_connect,
                )
                with dpg.theme() as green_theme:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(dpg.mvThemeCol_Button, (46, 204, 113, 220))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 204, 113, 255))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (27, 152, 79, 255))
                dpg.bind_item_theme(self._ids["connect_btn"], green_theme)

                dpg.add_spacer(width=8)
                dpg.add_button(
                    label="Cancel",
                    width=80,
                    height=32,
                    callback=self._cancel,
                )

            dpg.add_spacer(height=10)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _toggle_show(self, sender, app_data) -> None:
        key = self._ids.get("key_input")
        if key and dpg.does_item_exist(key):
            dpg.configure_item(key, password=not app_data)

    def _try_connect(self) -> None:
        key_tag = self._ids.get("key_input")
        status_tag = self._ids.get("status")
        btn_tag = self._ids.get("connect_btn")

        if not key_tag:
            return
        key = dpg.get_value(key_tag).strip()
        if not key:
            if status_tag:
                dpg.configure_item(status_tag, default_value="Please enter an access key.")
            return

        if status_tag:
            dpg.configure_item(status_tag, default_value="Connecting…", color=(200, 200, 80, 255))
        if btn_tag:
            dpg.configure_item(btn_tag, enabled=False)

        import threading
        import db_handler
        import config as _config

        def _worker():
            success = db_handler.init(_SUPABASE_URL, key)
            if success:
                _config.save_supabase(_SUPABASE_URL, key)
                dpg.split_frame()
                if dpg.does_item_exist(_DIALOG_TAG):
                    dpg.delete_item(_DIALOG_TAG)
                self._on_success(_SUPABASE_URL, key)
            else:
                msg = "Invalid key or connection failed. Please check and try again."
                if status_tag and dpg.does_item_exist(status_tag):
                    dpg.configure_item(status_tag, default_value=msg, color=(255, 80, 80, 255))
                if btn_tag and dpg.does_item_exist(btn_tag):
                    dpg.configure_item(btn_tag, enabled=True)

        threading.Thread(target=_worker, daemon=True).start()

    def _cancel(self) -> None:
        if dpg.does_item_exist(_DIALOG_TAG):
            dpg.delete_item(_DIALOG_TAG)
        self._on_cancel()
