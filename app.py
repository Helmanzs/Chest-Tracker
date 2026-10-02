"""Central application class: state and wiring between the log monitor, database and UI."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import dearpygui.dearpygui as dpg
import pandas as pd

import chest_definitions
import config
import db_cache
import db_handler
import excel_handler
import prices_config
import updater
from constants import (
    BOUNTY_TIER_GROUPS,
    CHEST_DATA_SHEETS,
    CHEST_DISPLAY_NAMES,
    DEFAULT_CHEST_TYPE,
    bounty_group_key,
)
from log_monitor import LogMonitor
from ui.dialogs import centered_pos, show_modal
from ui.fonts import find_system_font
from ui.prices_tab import PricesTab
from ui.tracker_tab import TrackerTab
from ui.viewer_tab import ViewerTab
from utils import fmt_number, lower_keys

APP_VERSION = "1.0.21"

_FRAME_SLEEP_S = 0.033  # ~30 fps

_ALL_BOUNTY_TIERS = frozenset(tier for tiers in BOUNTY_TIER_GROUPS.values() for tier in tiers)

_GLOBAL_THEME_COLORS = {
    dpg.mvThemeCol_WindowBg: (30, 30, 35, 255),
    dpg.mvThemeCol_ChildBg: (38, 38, 44, 255),
    dpg.mvThemeCol_FrameBg: (50, 50, 58, 255),
    dpg.mvThemeCol_FrameBgHovered: (60, 60, 70, 255),
    dpg.mvThemeCol_Button: (55, 100, 190, 220),
    dpg.mvThemeCol_ButtonHovered: (70, 120, 210, 255),
    dpg.mvThemeCol_ButtonActive: (40, 80, 160, 255),
    dpg.mvThemeCol_Header: (60, 90, 170, 220),
    dpg.mvThemeCol_HeaderHovered: (70, 110, 190, 255),
    dpg.mvThemeCol_Tab: (40, 40, 50, 255),
    dpg.mvThemeCol_TabActive: (55, 90, 170, 255),
    dpg.mvThemeCol_TabHovered: (70, 110, 190, 255),
    dpg.mvThemeCol_Text: (220, 220, 220, 255),
    dpg.mvThemeCol_Border: (70, 70, 80, 255),
    dpg.mvThemeCol_PopupBg: (35, 35, 42, 255),
    dpg.mvThemeCol_TitleBg: (30, 30, 40, 255),
    dpg.mvThemeCol_TitleBgActive: (40, 60, 120, 255),
    dpg.mvThemeCol_CheckMark: (100, 200, 120, 255),
    dpg.mvThemeCol_SliderGrab: (100, 160, 255, 255),
    dpg.mvThemeCol_TableHeaderBg: (40, 50, 80, 255),
    dpg.mvThemeCol_TableRowBg: (38, 38, 44, 255),
    dpg.mvThemeCol_TableRowBgAlt: (45, 45, 52, 255),
    dpg.mvThemeCol_TableBorderLight: (70, 70, 80, 255),
    dpg.mvThemeCol_TableBorderStrong: (90, 90, 100, 255),
}
_GLOBAL_THEME_STYLES = (
    (dpg.mvStyleVar_FrameRounding, (4,)),
    (dpg.mvStyleVar_ChildRounding, (4,)),
    (dpg.mvStyleVar_WindowRounding, (6,)),
    (dpg.mvStyleVar_FramePadding, (6, 4)),
    (dpg.mvStyleVar_ItemSpacing, (8, 6)),
    (dpg.mvStyleVar_CellPadding, (4, 3)),
)


@dataclass
class _Session:
    chest_ids: list[int] = field(default_factory=list)
    total_revenue: float = 0.0
    chest_count: int = 0

    @property
    def avg_revenue(self) -> float:
        return self.total_revenue / self.chest_count if self.chest_count else 0.0


# ---------------------------------------------------------------------------
# Main-thread callback queue (DPG calls must happen on the render thread)
# ---------------------------------------------------------------------------

_pending_callbacks: list[Callable[[], None]] = []
_pending_lock = threading.Lock()


def _queue(fn: Callable[[], None]) -> None:
    with _pending_lock:
        _pending_callbacks.append(fn)


def _flush_queue() -> None:
    with _pending_lock:
        callbacks = list(_pending_callbacks)
        _pending_callbacks.clear()
    for fn in callbacks:
        try:
            fn()
        except Exception as exc:
            print(f"[queue] callback error: {exc}")


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _load_unicode_font(size: int = 15) -> None:
    path = find_system_font()
    if path is None:
        print("[font] No Unicode TTF found -- falling back to built-in font")
        return
    try:
        with dpg.font_registry():
            font = dpg.add_font(path, size)
        dpg.bind_font(font)
    except Exception as exc:
        print(f"[font] Failed to load {path}: {exc}")


def _apply_global_theme() -> None:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            for colour_id, rgba in _GLOBAL_THEME_COLORS.items():
                dpg.add_theme_color(colour_id, rgba)
            for style_id, values in _GLOBAL_THEME_STYLES:
                dpg.add_theme_style(style_id, *values)
    dpg.bind_theme(theme)


class App:
    def __init__(self) -> None:
        self._log_path: str = config.load("log_path")
        self._selected_chest: str = config.load("chest_type") or DEFAULT_CHEST_TYPE

        self._item_prices: dict[str, float] = {}  # lower-case item -> price, for the selected chest
        self._all_prices: dict[str, dict[str, float]] = {}
        self._shard_avgs: dict[str, float] = {}
        self._db_connected = False

        self._monitor: LogMonitor | None = None
        self._session = _Session()

        # Debounces view refreshes so back-to-back writes cost one round-trip.
        self._refresh_pending = False
        self._refresh_lock = threading.Lock()

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction & main loop
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        dpg.create_context()
        _apply_global_theme()
        dpg.create_viewport(
            title=f"Multi-Chest Tracker v{APP_VERSION}",
            width=1000,
            height=780,
            min_width=800,
            min_height=600,
            resizable=True,
        )
        dpg.setup_dearpygui()
        _load_unicode_font()

        with dpg.window(tag="primary_window", no_title_bar=True, no_move=True, no_resize=True):
            with dpg.tab_bar():
                with dpg.tab(label=" Live Tracker ") as tracker_tab:
                    pass
                with dpg.tab(label=" Excel Data ") as viewer_tab:
                    pass
                with dpg.tab(label=" Prices ") as prices_tab:
                    pass

        chest_types = list(CHEST_DATA_SHEETS)
        self._tracker = TrackerTab(
            parent_tag=tracker_tab,
            on_start_stop=self._toggle_service,
            on_manual=self._manual_chest_trigger,
            on_log_browse=self._on_log_browse,
            initial_log_path=self._log_path,
        )
        self._viewer = ViewerTab(
            parent_tag=viewer_tab,
            chest_types=chest_types,
            on_refresh=self._refresh_db_view,
            on_reload_prices=self._reload_prices,
            on_export=self._export_to_excel,
            on_session_toggle=lambda _session_only: self._refresh_db_view(),
            on_chest_selected=self._on_viewer_chest_selected,
            initial_chest=self._selected_chest,
        )
        self._prices_tab = PricesTab(
            parent_tag=prices_tab,
            chest_types=chest_types,
            on_prices_changed=self._on_prices_changed,
        )

        dpg.set_primary_window("primary_window", True)
        dpg.show_viewport()

    def run(self) -> None:
        threading.Thread(target=self._startup, daemon=True).start()

        while dpg.is_dearpygui_running():
            dpg.configure_item(
                "primary_window",
                width=dpg.get_viewport_width(),
                height=dpg.get_viewport_height(),
            )
            _flush_queue()
            dpg.render_dearpygui_frame()
            time.sleep(_FRAME_SLEEP_S)

        dpg.destroy_context()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _startup(self) -> None:
        _queue(lambda: self._log(f"Chest definitions: {chest_definitions.DEFINITIONS_STATUS}", "gray"))
        threading.Thread(target=self._check_for_update, daemon=True).start()
        _queue(self._connect_db_and_load if config.has_supabase_config() else self._show_setup_dialog)

    def _show_setup_dialog(self, existing_key: str = "") -> None:
        from ui.setup_dialog import SetupDialog

        def on_success(url: str, key: str) -> None:
            self._db_connected = True
            self._log("Connected to Supabase [ok]", "green")
            self._post_connect_startup()

        def on_cancel() -> None:
            self._log("No Supabase key provided -- database features disabled. Restart to try again.", "orange")
            self._load_all_prices_startup()
            self._tracker.set_chest_types(list(CHEST_DATA_SHEETS))

        SetupDialog(on_success=on_success, on_cancel=on_cancel, existing_key=existing_key)

    def _connect_db_and_load(self) -> None:
        url = config.load("supabase_url")
        key = config.load("supabase_key")

        def on_connected() -> None:
            self._db_connected = True
            self._log("Connected to Supabase [ok]", "green")
            self._post_connect_startup()

        def on_failed() -> None:
            self._log("Supabase connection failed -- re-enter your key.", "red")
            self._show_setup_dialog(existing_key=key)

        def worker() -> None:
            _queue(on_connected if db_handler.init(url, key) else on_failed)

        threading.Thread(target=worker, daemon=True).start()

    def _post_connect_startup(self) -> None:
        self._load_all_prices_startup()
        self._refresh_db_view()
        self._tracker.set_chest_types(list(CHEST_DATA_SHEETS))
        if self._db_connected:
            self._prices_tab.set_loading(list(CHEST_DATA_SHEETS))
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    def _load_all_prices_startup(self) -> None:
        self._all_prices = prices_config.load_all_prices()
        for chest_type in CHEST_DATA_SHEETS:
            count = len(self._all_prices.get(chest_type, {}))
            if count:
                self._log(f"Loaded {count} prices for '{chest_type}'", "green")
            else:
                self._log(f"No prices set for '{chest_type}' -- set them in the Prices tab.", "orange")

        viewer_chest = self._viewer.selected_chest() or self._selected_chest
        self._item_prices = lower_keys(self._all_prices.get(viewer_chest, {}))
        self._tracker.set_item_prices(self._item_prices)

    def _startup_drop_rates(self) -> None:
        """Fetch stats, drop rates and averages for every chest type in one batched scan."""
        all_stats, all_rates, all_avgs = db_handler.fetch_all_stats_batch(list(CHEST_DATA_SHEETS), self._all_prices)

        for chest_type in CHEST_DATA_SHEETS:
            shard_avg = all_avgs.get(chest_type, {}).get("Shard")
            if shard_avg is not None:
                self._shard_avgs[chest_type] = shard_avg

        def apply() -> None:
            for chest_type, stats in all_stats.items():
                if stats.total_chests > 0:
                    short = CHEST_DISPLAY_NAMES.get(chest_type, chest_type)
                    self._log(
                        f"{short}: {stats.total_chests} chests -- avg {fmt_number(stats.avg_revenue_per_chest)}",
                        "gray",
                    )
            self._prices_tab.apply_drop_rates(all_rates, all_stats, all_avgs)

        _queue(apply)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def _check_for_update(self) -> None:
        result = updater.check_for_update(APP_VERSION)
        if result.error:
            _queue(lambda: self._log(f"[updater] {result.error}", "gray"))
        elif result.update_available:
            _queue(lambda: self._prompt_update(result))
        else:
            _queue(lambda: self._log(f"App is up to date (v{APP_VERSION})", "gray"))

    def _prompt_update(self, result: updater.UpdateResult) -> None:
        message = (
            "A new version is available!\n\n"
            f"  Current:  v{result.current_version}\n"
            f"  Latest:   {result.latest_version}\n"
        )
        if result.release_notes:
            message += f"\nChanges:\n{result.release_notes}\n"
        message += "\nUpdate and restart now?"

        def close() -> None:
            if dpg.does_item_exist("update_modal"):
                dpg.delete_item("update_modal")

        def start_update() -> None:
            close()
            self._log("Downloading update...", "blue")
            updater.download_and_replace(
                result,
                on_progress=lambda m: _queue(lambda: self._log(m, "blue")),
                on_complete=self._on_update_complete,
            )

        close()
        with dpg.window(
            tag="update_modal",
            label="Update Available",
            modal=True,
            no_resize=True,
            width=460,
            pos=centered_pos(460, 280),
        ):
            dpg.add_text(message, wrap=440)
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Update Now", width=120, callback=start_update)
                dpg.add_spacer(width=8)
                dpg.add_button(label="Later", width=80, callback=close)

    def _on_update_complete(self, success: bool, message: str) -> None:
        _queue(lambda: self._log(message, "green" if success else "red"))
        if success:
            _queue(
                lambda: show_modal("Update Ready", f"{message}\n\nClose and reopen the app to use the new version.")
            )

    # ------------------------------------------------------------------
    # Log file & service control
    # ------------------------------------------------------------------

    def _on_log_browse(self, path: str) -> None:
        self._log_path = path
        self._log(f"Log file selected: {path}", "blue")
        self._save_config()

    def _create_monitor(self, log_path: str) -> LogMonitor:
        return LogMonitor(
            log_path=log_path,
            chest_types=CHEST_DATA_SHEETS,
            on_chest_detected=self._on_chest_detected,
            on_loot_item=self._on_loot_item,
            on_log=self._log_threadsafe,
            on_timeout=self._on_loot_timeout,
            on_pattern_chest=self._on_pattern_chest_detected,
        )

    def _toggle_service(self) -> None:
        if self._monitor and self._monitor.is_running:
            self._stop_service()
        else:
            self._start_service()

    def _start_service(self) -> None:
        if not self._log_path or not os.path.exists(self._log_path):
            show_modal("Log File Missing", "Please select a valid log file first!")
            return
        if not self._db_connected:
            show_modal("Not Connected", "Not connected to Supabase.\nCheck your access key in tracker_config.txt")
            return
        if not self._item_prices:
            self._load_prices()

        self._monitor = self._create_monitor(self._log_path)
        self._monitor.start()
        self._session = _Session()
        self._tracker.set_listening(True)
        self._tracker.set_status("Listening...", "green")
        self._log("=== SERVICE STARTED === Listening for all chest types...", "green")

    def _stop_service(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._tracker.set_listening(False)
        self._tracker.set_status("Stopped", "red")
        self._log("=== SERVICE STOPPED ===", "red")

    def _manual_chest_trigger(self, chest_type: str) -> None:
        if not self._db_connected:
            show_modal("Not Connected", "Not connected to Supabase!")
            return
        if not self._item_prices or self._selected_chest != chest_type:
            self._selected_chest = chest_type
            self._load_prices()
            self._tracker.set_sheet_label(CHEST_DATA_SHEETS.get(chest_type, ""))

        if self._monitor is None:
            self._monitor = self._create_monitor(self._log_path or "")

        self._on_chest_detected(chest_type)
        self._log("Manual chest tracking started. Waiting for timeout...", "purple")

        if not self._monitor.is_running:
            threading.Thread(target=self._manual_timeout_loop, daemon=True).start()

    def _manual_timeout_loop(self) -> None:
        """Without a running log tail nothing else fires the timeout, so poll for it."""
        monitor = self._monitor
        assert monitor is not None
        while monitor.awaiting_loot:
            if monitor.is_loot_stale():
                self._log_threadsafe(f"Loot collection timeout ({monitor.loot_timeout}s). Saving...", "orange")
                self._on_loot_timeout()
                break
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # LogMonitor callbacks (called from background threads)
    # ------------------------------------------------------------------

    def _set_active_chest(self, chest_name: str) -> None:
        """Make *chest_name* the active chest and start a fresh session (state only, no widgets)."""
        self._selected_chest = chest_name
        self._item_prices = lower_keys(self._all_prices.get(chest_name, {}))
        self._session = _Session()

    def _sync_chest_widgets(self, chest_name: str) -> None:
        """Reflect the active chest in the UI. Main thread only."""
        self._tracker.set_item_prices(self._item_prices)
        self._tracker.set_sheet_label(CHEST_DATA_SHEETS.get(chest_name, ""))
        self._viewer.set_selected_chest(chest_name)

    def _on_chest_detected(self, chest_name: str) -> None:
        if self._monitor is None:
            return

        pending = self._monitor.finalize()
        if pending:
            self._log_threadsafe("Saving previous chest data...", "orange")
            threading.Thread(target=self._write_loot_to_db, args=(pending,), daemon=True).start()

        if chest_name != self._selected_chest:
            # State changes immediately so the loot callbacks that follow see the new chest.
            self._set_active_chest(chest_name)
            _queue(lambda: self._sync_chest_widgets(chest_name))
            self._save_config()

        self._monitor.start_new_chest()
        self._log_threadsafe("\n" + "=" * 50, "blue")
        self._log_threadsafe(f"[!] {chest_name.upper()} DETECTED! Waiting for loot...", "blue")
        self._log_threadsafe("=" * 50, "blue")

    def _on_pattern_chest_detected(self, chest_name: str, loot: list[tuple[int, str]]) -> None:
        is_bounty = bounty_group_key(chest_name) is not None

        def resolve_and_write() -> None:
            effective_chest = chest_name

            if is_bounty:
                self._tracker.update_bounty_override_options(chest_name)
                override = self._tracker.get_bounty_override()
                if override in _ALL_BOUNTY_TIERS and override != chest_name:
                    effective_chest = override
                    self._log(f"[bounty] Override active: recording as '{effective_chest}'", "orange")

            self._log(f"[!] {effective_chest.upper()} DETECTED (pattern match)!", "blue")

            if effective_chest != self._selected_chest:
                self._set_active_chest(effective_chest)
                self._sync_chest_widgets(effective_chest)

            threading.Thread(target=self._write_loot_to_db, args=(loot, effective_chest), daemon=True).start()

            if is_bounty:
                self._tracker.reset_bounty_override_hint()

        _queue(resolve_and_write)

    def _on_loot_item(self, qty: int, item: str) -> None:
        self._log_threadsafe(f" + Found: {qty}x {item}", self._tracker.get_item_colour(item))

    def _on_loot_timeout(self) -> None:
        if self._monitor is None:
            return
        loot = self._monitor.finalize()
        if not loot:
            self._log_threadsafe("No loot to save.", "gray")
            return
        self._log_threadsafe(f"Finalizing {len(loot)} items...", "blue")
        threading.Thread(target=self._write_loot_to_db, args=(loot,), daemon=True).start()

    # ------------------------------------------------------------------
    # Saving loot
    # ------------------------------------------------------------------

    def _validate_loot(self, loot: list[tuple[int, str]]) -> str | None:
        """Return an error message if the loot looks incomplete or implausible."""
        shard_qty = next((qty for qty, item in loot if item.strip().lower() == "shard"), None)
        if not shard_qty:
            return "Shard quantity is 0 -- chest data looks incomplete. Not saved."

        avg = self._shard_avgs.get(self._selected_chest)
        if avg and shard_qty > avg * 3:
            return f"Shard quantity {shard_qty} is more than 3x the average ({avg:.0f}). Looks like an error -- not saved."
        return None

    def _write_loot_to_db(self, loot: list[tuple[int, str]], chest_type_override: str | None = None) -> None:
        chest_type = chest_type_override or self._selected_chest
        item_prices = lower_keys(self._all_prices.get(chest_type, self._item_prices))

        error = self._validate_loot(loot)
        if error:
            self._log_threadsafe(f"! Validation failed: {error}", "red")
            return

        result = db_handler.write_chest_loot(chest_type=chest_type, loot=loot, item_prices=item_prices)

        if not result.success:
            message = (
                "Not connected to Supabase -- chest data was NOT saved!"
                if result.error == "NOT_CONNECTED"
                else f"Error saving to Supabase: {result.error}"
            )
            self._log_threadsafe(message, "red")
            _queue(lambda: show_modal("Save Error", message))
            return

        for _, item in loot:
            self._log_threadsafe(f"  -> {item}", self._tracker.get_item_colour(item))

        self._log_threadsafe(f"[ok] Chest #{result.chest_number} saved to Supabase!", "green")
        if result.chest_revenue > 0:
            self._log_threadsafe(f"Revenue: {fmt_number(result.chest_revenue)}", "green")
            name, value = result.most_expensive_item
            if value > 0:
                self._log_threadsafe(f"Top item: {name} ({fmt_number(value)})", "green")
        self._log_threadsafe("=" * 50 + "\n", "green")

        self._session.chest_ids.append(result.chest_id)
        self._session.total_revenue += result.chest_revenue
        self._session.chest_count += 1
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Coalesce rapid writes into a single view refresh."""
        with self._refresh_lock:
            if self._refresh_pending:
                return
            self._refresh_pending = True

        def delayed() -> None:
            time.sleep(0.5)
            with self._refresh_lock:
                self._refresh_pending = False
            _queue(self._refresh_db_view)

        threading.Thread(target=delayed, daemon=True).start()

    # ------------------------------------------------------------------
    # Data viewer
    # ------------------------------------------------------------------

    def _refresh_db_view(self) -> None:
        if self._db_connected:
            threading.Thread(target=self._refresh_db_view_worker, daemon=True).start()

    def _refresh_db_view_worker(self) -> None:
        try:
            chest_type = self._viewer.selected_chest() or self._selected_chest
            prices = lower_keys(self._all_prices.get(chest_type, self._item_prices))
            session_ids = list(self._session.chest_ids)

            total_stats = db_handler.calculate_statistics(chest_type, prices)  # cached

            if self._viewer.is_session_mode() and session_ids:
                session_stats = db_handler.calculate_statistics_for_ids(session_ids, prices)
                loot_rows = db_handler.fetch_chests_by_ids(session_ids)
            else:
                session_stats = total_stats
                loot_rows = db_handler.fetch_all_loot(chest_type)  # cached

            _queue(lambda: self._apply_db_view(session_stats, total_stats, loot_rows, prices))
        except Exception as exc:
            self._log_threadsafe(f"Refresh error: {exc}", "red")

    def _apply_db_view(
        self,
        session_stats: db_handler.Stats,
        total_stats: db_handler.Stats,
        loot_rows: list[dict],
        item_prices: dict[str, float],
    ) -> None:
        self._viewer.show_stats(session_stats, total_stats)

        if not loot_rows:
            self._viewer.load_dataframe(pd.DataFrame(), item_prices)
            self._log(f"No chests recorded yet for '{self._selected_chest}' -- ready to track!", "gray")
            return

        self._viewer.load_dataframe(excel_handler.pivot_loot(loot_rows), item_prices)

        s, t = session_stats, total_stats
        if s.total_chests != t.total_chests:
            chests = f"{s.total_chests} ({t.total_chests})"
            avg = f"{fmt_number(s.avg_revenue_per_chest)} ({fmt_number(t.avg_revenue_per_chest)})"
        else:
            chests = str(s.total_chests)
            avg = fmt_number(s.avg_revenue_per_chest)
        self._log(f"Loaded {chests} chests -- avg {avg}, total {fmt_number(s.total_revenue)}", "gray")

    def _on_viewer_chest_selected(self, chest_type: str) -> None:
        self._item_prices = lower_keys(self._all_prices.get(chest_type, {}))
        self._refresh_db_view()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def _load_prices(self) -> None:
        prices = prices_config.load_prices(self._selected_chest)
        self._all_prices[self._selected_chest] = prices
        self._item_prices = lower_keys(prices)
        self._tracker.set_item_prices(self._item_prices)
        if self._item_prices:
            self._log(f"Loaded {len(self._item_prices)} prices for '{self._selected_chest}'", "green")
        else:
            self._log(f"No prices set for '{self._selected_chest}' -- set them in the Prices tab.", "orange")

    def _reload_prices(self) -> None:
        self._load_prices()
        self._refresh_db_view()

    def _on_prices_changed(self, all_prices: dict[str, dict[str, float]]) -> None:
        self._all_prices = all_prices
        self._item_prices = lower_keys(all_prices.get(self._selected_chest, {}))
        self._tracker.set_item_prices(self._item_prices)
        self._log(f"Prices updated: {len(self._item_prices)} items for '{self._selected_chest}'", "green")

        db_cache.invalidate_all()  # cached revenue figures are now stale
        self._refresh_db_view()
        if self._db_connected:
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_to_excel(self) -> None:
        if not self._db_connected:
            show_modal("Not Connected", "Not connected to Supabase!")
            return

        chest_type = self._viewer.selected_chest() or self._selected_chest
        safe_name = chest_type.replace("'", "").replace(" ", "_")

        def close_dialog() -> None:
            if dpg.does_item_exist("export_file_dialog"):
                dpg.delete_item("export_file_dialog")

        def on_selected(sender, app_data) -> None:
            path = app_data.get("file_path_name", "")
            if path:
                threading.Thread(target=self._export_worker, args=(path,), daemon=True).start()
            close_dialog()

        close_dialog()
        dpg.add_file_dialog(
            tag="export_file_dialog",
            label="Export to Excel",
            width=700,
            height=450,
            modal=True,
            default_filename=f"{safe_name}_export.xlsx",
            callback=on_selected,
            cancel_callback=lambda s, a: close_dialog(),
        )
        dpg.add_file_extension(".xlsx", parent="export_file_dialog")

    def _export_worker(self, path: str) -> None:
        try:
            chest_type = self._viewer.selected_chest() or self._selected_chest
            loot_rows = db_handler.fetch_all_loot(chest_type)
            if not loot_rows:
                _queue(lambda: show_modal("No Data", "No chests recorded yet to export."))
                return

            prices = self._all_prices.get(chest_type, {})
            pinned_lower = [p.lower() for p in prices_config.load_pinned_items(chest_type)]

            def column_sort_key(name: str) -> tuple[int, float]:
                lower = name.lower()
                if lower in pinned_lower:
                    return (pinned_lower.index(lower), 0.0)
                return (len(pinned_lower), -prices.get(name, 0.0))

            saved_to = excel_handler.export_to_excel(
                chest_type,
                loot_rows,
                drop_rates=db_handler.fetch_drop_rates(chest_type),
                column_order=sorted(prices, key=column_sort_key),
                output_path=path,
            )
            self._log_threadsafe(f"Exported to {saved_to}", "green")
            _queue(lambda: show_modal("Export Complete", f"Saved to:\n{saved_to}"))
        except Exception as exc:
            message = f"Export failed: {exc}"
            self._log_threadsafe(message, "red")
            _queue(lambda: show_modal("Export Error", message))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        config.save({"log_path": self._log_path, "chest_type": self._selected_chest})

    def _log(self, message: str, colour: str = "black") -> None:
        self._tracker.log(message, colour)

    def _log_threadsafe(self, message: str, colour: str = "black") -> None:
        _queue(lambda: self._tracker.log(message, colour))
