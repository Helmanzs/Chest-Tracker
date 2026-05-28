"""
app.py  (Dear PyGui port)
--------------------------
Central application class.  All state + wiring.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

import dearpygui.dearpygui as dpg

import config
import prices_config
import db_handler
import excel_handler
from constants import (
    CHEST_DATA_SHEETS,
    CHEST_DISPLAY_NAMES,
    DEFAULT_CHEST_TYPE,
    BOUNTY_TIER_GROUPS,
    LOOT_TIMEOUT,
)
from log_monitor import LogMonitor
from ui.mini_window import MiniWindow
from ui.tracker_tab import TrackerTab, _show_modal
from ui.prices_tab import PricesTab
from ui.viewer_tab import ViewerTab
import updater

APP_VERSION = "1.0.16"


@dataclass
class _Session:
    chest_ids: list[int] = field(default_factory=list)
    total_revenue: float = 0.0
    chest_count: int = 0

    @property
    def avg_revenue(self) -> float:
        return self.total_revenue / self.chest_count if self.chest_count else 0.0


# pystray / PIL support -- both are optional
try:
    import pystray as _pystray
    from PIL import Image as _PILImage, ImageDraw as _PILImageDraw

    _TRAY_AVAILABLE = True
except ImportError:
    _pystray = None  # type: ignore[assignment]
    _PILImage = None  # type: ignore[assignment]
    _PILImageDraw = None  # type: ignore[assignment]
    _TRAY_AVAILABLE = False


def _make_tray_icon_image(size: int = 64):  # type: ignore[return]
    """Build a small green-circle PIL image for the system tray."""
    if _PILImage is None or _PILImageDraw is None:
        return None
    img = _PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = _PILImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill="#2ecc71", outline="#27ae60", width=3)
    return img


# Global callback queue: fn tuples executed on the main DPG thread
_CALLBACK_QUEUE: list = []
_QUEUE_LOCK = threading.Lock()


def _queue(fn) -> None:
    """Schedule *fn* to run on the next DPG frame (thread-safe)."""
    with _QUEUE_LOCK:
        _CALLBACK_QUEUE.append(fn)


def _flush_queue() -> None:
    with _QUEUE_LOCK:
        fns = list(_CALLBACK_QUEUE)
        _CALLBACK_QUEUE.clear()
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            print(f"[queue] callback error: {exc}")


def _load_unicode_font(size: int = 15) -> None:
    """
    Load a system font with full Unicode coverage and bind it globally.
    Falls back silently to DPG's built-in ASCII font if no TTF is found.
    """
    import sys

    candidates: list[str] = []
    if sys.platform == "win32":
        candidates = [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\tahoma.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/SFNSText.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]

    font_path = next((p for p in candidates if os.path.isfile(p)), None)
    if font_path is None:
        print("[font] No Unicode TTF found -- falling back to built-in font")
        return

    try:
        with dpg.font_registry():
            font = dpg.add_font(font_path, size)
        dpg.bind_font(font)
        print(f"[font] Loaded Unicode font: {font_path}")
    except Exception as exc:
        print(f"[font] Failed to load {font_path}: {exc}")


# ---------------------------------------------------------------------------
# Bounty group helpers (module-level, no DPG imports)
# ---------------------------------------------------------------------------


def _bounty_group_key(chest_name: str) -> str | None:
    """Return the canonical BOUNTY_TIER_GROUPS key for *chest_name*, or None."""
    if chest_name in BOUNTY_TIER_GROUPS:
        return chest_name
    for key, tiers in BOUNTY_TIER_GROUPS.items():
        if chest_name in tiers:
            return key
    return None


class App:
    """Root application controller (Dear PyGui)."""

    def __init__(self) -> None:
        # -- Persisted settings ---------------------------------------
        self._log_path: str = config.load("log_path")
        self._selected_chest: str = config.load("chest_type") or DEFAULT_CHEST_TYPE

        # -- Runtime state --------------------------------------------
        self._item_prices: dict[str, float] = {}
        self._all_prices: dict[str, dict[str, float]] = {}
        self._shard_avgs: dict[str, float] = {}
        self._db_connected: bool = False

        self._last_most_expensive: tuple[str, float] = ("-", 0.0)
        self._mini_avg_revenue: float = 0.0

        self._monitor: LogMonitor | None = None
        self._tray_icon = None
        self._session: _Session = _Session()
        self._mini: MiniWindow | None = None
        self._mini_mode_active: bool = False

        # -- Build DPG UI ---------------------------------------------
        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        dpg.create_context()

        # Global dark theme
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (38, 38, 44, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (50, 50, 58, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (60, 60, 70, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 100, 190, 220))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 120, 210, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 80, 160, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (60, 90, 170, 220))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (70, 110, 190, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Tab, (40, 40, 50, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TabActive, (55, 90, 170, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (70, 110, 190, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 220, 220, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (70, 70, 80, 255))
                dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (35, 35, 42, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (30, 30, 40, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (40, 60, 120, 255))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (100, 200, 120, 255))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (100, 160, 255, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (40, 50, 80, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (38, 38, 44, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (45, 45, 52, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, (70, 70, 80, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, (90, 90, 100, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
                dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 4, 3)
        dpg.bind_theme(global_theme)

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
                with dpg.tab(label=" Live Tracker ") as tab_tracker:
                    pass
                with dpg.tab(label=" Excel Data ") as tab_viewer:
                    pass
                with dpg.tab(label=" Prices ") as tab_prices:
                    pass

        self._tracker = TrackerTab(
            parent_tag=tab_tracker,
            on_start_stop=self._toggle_service,
            on_manual=self._manual_chest_trigger,
            on_mini_toggle=self._toggle_mini,
            on_log_browse=self._on_log_browse,
            initial_log_path=self._log_path,
        )
        self._viewer = ViewerTab(
            parent_tag=tab_viewer,
            chest_types=list(CHEST_DATA_SHEETS.keys()),
            on_refresh=self._refresh_db_view,
            on_reload_prices=self._reload_prices,
            on_export=self._export_to_excel,
            on_session_toggle=self._on_session_toggle,
            on_chest_selected=self._on_viewer_chest_selected,
            initial_chest=self._selected_chest,
        )
        self._prices_tab = PricesTab(
            parent_tag=tab_prices,
            chest_types=list(CHEST_DATA_SHEETS.keys()),
            on_prices_changed=self._on_prices_changed,
        )

        dpg.set_primary_window("primary_window", True)
        dpg.show_viewport()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the DPG render loop and run startup logic."""
        threading.Thread(target=self._startup, daemon=True).start()

        while dpg.is_dearpygui_running():
            vw = dpg.get_viewport_width()
            vh = dpg.get_viewport_height()
            dpg.configure_item("primary_window", width=vw, height=vh)

            _flush_queue()
            dpg.render_dearpygui_frame()

        dpg.destroy_context()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _startup(self) -> None:
        threading.Thread(target=self._check_for_update, daemon=True).start()
        if config.has_supabase_config():
            _queue(self._connect_db_and_load)
        else:
            _queue(self._show_setup_dialog)

    def _show_setup_dialog(self, existing_key: str = "") -> None:
        from ui.setup_dialog import SetupDialog

        def on_success(url: str, key: str) -> None:
            self._db_connected = True
            self._log("Connected to Supabase [ok]", "green")
            self._post_connect_startup()

        def on_cancel() -> None:
            self._log(
                "No Supabase key provided -- database features disabled. " "Restart to try again.",
                "orange",
            )
            self._load_all_prices_startup()
            self._tracker.set_chest_types(list(CHEST_DATA_SHEETS.keys()))

        SetupDialog(
            on_success=on_success,
            on_cancel=on_cancel,
            existing_key=existing_key,
        )

    def _connect_db_and_load(self) -> None:
        url = config.load("supabase_url")
        key = config.load("supabase_key")

        def _worker():
            connected = db_handler.init(url, key)
            if connected:

                def _ok():
                    self._db_connected = True
                    self._log("Connected to Supabase [ok]", "green")
                    self._post_connect_startup()

                _queue(_ok)
            else:

                def _fail():
                    self._log("Supabase connection failed -- re-enter your key.", "red")
                    self._show_setup_dialog(existing_key=key)

                _queue(_fail)

        threading.Thread(target=_worker, daemon=True).start()

    def _post_connect_startup(self) -> None:
        self._load_all_prices_startup()
        self._refresh_db_view()
        self._tracker.set_chest_types(list(CHEST_DATA_SHEETS.keys()))
        if self._db_connected:
            self._prices_tab.set_loading(list(CHEST_DATA_SHEETS.keys()))
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    def _load_all_prices_startup(self) -> None:
        self._all_prices = prices_config.load_all_prices()
        for chest_type in CHEST_DATA_SHEETS:
            prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, {}).items()}
            count = len(prices)
            if count:
                self._log(f"Loaded {count} prices for '{chest_type}'", "green")
            else:
                self._log(f"No prices set for '{chest_type}' -- set them in the Prices tab.", "orange")
        viewer_chest = self._viewer.selected_chest() or self._selected_chest
        self._item_prices = {k.lower(): v for k, v in self._all_prices.get(viewer_chest, {}).items()}
        self._tracker.set_item_prices(self._item_prices)

    def _startup_drop_rates(self) -> None:
        all_rates: dict[str, dict[str, float]] = {}
        all_avgs: dict[str, dict[str, float]] = {}
        all_stats: dict[str, db_handler.Stats] = {}
        for chest_type in CHEST_DATA_SHEETS:
            all_rates[chest_type] = db_handler.fetch_drop_rates(chest_type)
            all_avgs[chest_type] = db_handler.fetch_avg_quantities(chest_type)
            prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, {}).items()}
            all_stats[chest_type] = db_handler.calculate_statistics(chest_type, prices)
            shard_avg = db_handler.fetch_item_avg(chest_type, "Shard")
            if shard_avg is not None:
                self._shard_avgs[chest_type] = shard_avg

        def _apply():
            for ct, st in all_stats.items():
                short = CHEST_DISPLAY_NAMES.get(ct, ct.replace("'s Chest", "").replace(" Chest", "").strip())
                if st.total_chests > 0:
                    self._log(
                        f"{short}: {st.total_chests} chests -- avg {self._fmt(st.avg_revenue_per_chest)}",
                        "gray",
                    )
            self._prices_tab.apply_drop_rates(all_rates, all_stats, all_avgs)

        _queue(_apply)

    def _check_for_update(self) -> None:
        result = updater.check_for_update(APP_VERSION)
        if result.error:
            _queue(lambda: self._log(f"[updater] {result.error}", "gray"))
            return
        if not result.update_available:
            _queue(lambda: self._log(f"App is up to date (v{APP_VERSION})", "gray"))
            return
        _queue(lambda: self._prompt_update(result))

    def _prompt_update(self, result: updater.UpdateResult) -> None:
        msg = (
            f"A new version is available!\n\n"
            f"  Current:  v{result.current_version}\n"
            f"  Latest:   {result.latest_version}\n"
        )
        if result.release_notes:
            msg += f"\nChanges:\n{result.release_notes}\n"
        msg += "\nUpdate and restart now?"

        if dpg.does_item_exist("update_modal"):
            dpg.delete_item("update_modal")

        vw = dpg.get_viewport_width()
        vh = dpg.get_viewport_height()
        with dpg.window(
            tag="update_modal",
            label="Update Available",
            modal=True,
            no_resize=True,
            width=460,
            pos=[max(0, (vw - 460) // 2), max(0, (vh - 280) // 2)],
        ):
            dpg.add_text(msg, wrap=440)
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):

                def _do_update():
                    if dpg.does_item_exist("update_modal"):
                        dpg.delete_item("update_modal")
                    self._log("Downloading update...", "blue")
                    updater.download_and_replace(
                        result,
                        on_progress=lambda m: _queue(lambda msg=m: self._log(msg, "blue")),
                        on_complete=self._on_update_complete,
                    )

                dpg.add_button(label="Update Now", width=120, callback=_do_update)
                dpg.add_spacer(width=8)
                dpg.add_button(
                    label="Later",
                    width=80,
                    callback=lambda: dpg.delete_item("update_modal") if dpg.does_item_exist("update_modal") else None,
                )

    def _on_update_complete(self, success: bool, message: str) -> None:
        colour = "green" if success else "red"
        _queue(lambda: self._log(message, colour))
        if success:
            _queue(
                lambda: _show_modal(
                    "Update Ready",
                    f"{message}\n\nClose and reopen the app to use the new version.",
                )
            )

    # ------------------------------------------------------------------
    # File browsing
    # ------------------------------------------------------------------

    def _on_log_browse(self, path: str) -> None:
        self._log_path = path
        self._log(f"Log file selected: {path}", "blue")
        self._save_config()

    # ------------------------------------------------------------------
    # Service start / stop
    # ------------------------------------------------------------------

    def _toggle_service(self) -> None:
        if self._monitor and self._monitor.is_running:
            self._stop_service()
        else:
            self._start_service()

    def _start_service(self) -> None:
        if not self._log_path or not os.path.exists(self._log_path):
            _show_modal("Log File Missing", "Please select a valid log file first!")
            return
        if not self._db_connected:
            _show_modal(
                "Not Connected",
                "Not connected to Supabase.\nCheck your access key in tracker_config.txt",
            )
            return
        if not self._item_prices:
            self._load_prices()

        self._monitor = LogMonitor(
            log_path=self._log_path,
            chest_types=CHEST_DATA_SHEETS,
            on_chest_detected=self._on_chest_detected,
            on_loot_item=self._on_loot_item,
            on_log=self._log_threadsafe,
            on_timeout=self._on_loot_timeout,
            on_pattern_chest=self._on_pattern_chest_detected,
        )
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

    # ------------------------------------------------------------------
    # Manual chest trigger
    # ------------------------------------------------------------------

    def _manual_chest_trigger(self, chest_type: str) -> None:
        if not self._db_connected:
            _show_modal("Not Connected", "Not connected to Supabase!")
            return
        if not self._item_prices or self._selected_chest != chest_type:
            self._selected_chest = chest_type
            self._load_prices()
            self._tracker.set_sheet_label(CHEST_DATA_SHEETS.get(chest_type, ""))

        if self._monitor is None:
            self._monitor = LogMonitor(
                log_path=self._log_path or "",
                chest_types=CHEST_DATA_SHEETS,
                on_chest_detected=self._on_chest_detected,
                on_loot_item=self._on_loot_item,
                on_log=self._log_threadsafe,
                on_timeout=self._on_loot_timeout,
                on_pattern_chest=self._on_pattern_chest_detected,
            )

        self._on_chest_detected(chest_type)
        self._log("Manual chest tracking started. Waiting for timeout...", "purple")

        if not self._monitor.is_running:
            threading.Thread(target=self._manual_timeout_loop, daemon=True).start()

    def _manual_timeout_loop(self) -> None:
        import time

        assert self._monitor is not None
        while self._monitor._awaiting_loot:
            loot = self._monitor.captured_loot
            last = self._monitor._last_loot_time
            ts = self._monitor._target_timestamp
            if last and ts and loot:
                if time.time() - last >= LOOT_TIMEOUT:
                    self._log_threadsafe(f"Loot collection timeout ({LOOT_TIMEOUT}s). Saving...", "orange")
                    self._on_loot_timeout()
                    break
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # LogMonitor callbacks
    # ------------------------------------------------------------------

    def _on_chest_detected(self, chest_name: str) -> None:
        if self._monitor is None:
            return
        pending = self._monitor.finalize()
        if pending:
            self._log_threadsafe("Saving previous chest data...", "orange")
            threading.Thread(target=self._write_loot_to_db, args=(pending,), daemon=True).start()

        if chest_name != self._selected_chest:
            self._selected_chest = chest_name
            self._item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_name, {}).items()}

            def _sync_ui():
                self._tracker.set_item_prices(self._item_prices)
                self._tracker.set_sheet_label(CHEST_DATA_SHEETS.get(chest_name, ""))
                self._viewer.set_selected_chest(chest_name)

            _queue(_sync_ui)
            self._mini_avg_revenue = 0.0
            self._session = _Session()
            self._save_config()

        self._monitor.start_new_chest()
        self._log_threadsafe("\n" + "=" * 50, "blue")
        self._log_threadsafe(f"[!] {chest_name.upper()} DETECTED! Waiting for loot...", "blue")
        self._log_threadsafe("=" * 50, "blue")
        _queue(self._update_mini)

    def _on_pattern_chest_detected(self, chest_name: str, loot: list[tuple[int, str]]) -> None:
        """
        Called by LogMonitor when a pattern-matched chest is found.

        For bounty chests the user may have pre-selected a different tier
        (e.g. Portal Bounty) via the override dropdown.  We read that
        selection on the main thread and use it in place of the raw
        detected name before writing to the DB.
        """
        group_key = _bounty_group_key(chest_name)
        is_bounty = group_key is not None

        # Build a flat set of every valid bounty tier name for the override check.
        _all_bounty_tiers: frozenset[str] = frozenset(t for tiers in BOUNTY_TIER_GROUPS.values() for t in tiers)

        def _resolve_and_write(loot=loot):
            effective_chest = chest_name

            if is_bounty:
                self._tracker.update_bounty_override_options(chest_name)

                override = self._tracker.get_bounty_override()
                # Accept any valid tier — user may intentionally cross-select
                # (e.g. override a detected Normal bounty to record as Heroic).
                if override and override in _all_bounty_tiers:
                    effective_chest = override
                    if effective_chest != chest_name:
                        self._log(
                            f"[bounty] Override active: recording as '{effective_chest}'",
                            "orange",
                        )

            self._log(f"[!] {effective_chest.upper()} DETECTED (pattern match)!", "blue")

            if effective_chest != self._selected_chest:
                self._selected_chest = effective_chest
                self._item_prices = {k.lower(): v for k, v in self._all_prices.get(effective_chest, {}).items()}
                self._tracker.set_item_prices(self._item_prices)
                self._viewer.set_selected_chest(effective_chest)
                self._mini_avg_revenue = 0.0
                self._session = _Session()

            threading.Thread(
                target=self._write_loot_to_db,
                args=(loot, effective_chest),
                daemon=True,
            ).start()

            if is_bounty:
                self._tracker.reset_bounty_override_hint()

        _queue(_resolve_and_write)

    def _on_loot_item(self, qty: int, item: str) -> None:
        colour = self._tracker.get_item_colour(item)
        self._log_threadsafe(f" + Found: {qty}x {item}", colour)
        _queue(self._update_mini)

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
    # DB writing
    # ------------------------------------------------------------------

    def _validate_loot(self, loot: list[tuple[int, str]]) -> str | None:
        """Return an error message if loot looks invalid, or None if OK."""
        shard_qty = next((qty for qty, item in loot if item.strip().lower() == "shard"), None)
        if shard_qty is None or shard_qty == 0:
            return "Shard quantity is 0 -- chest data looks incomplete. Not saved."
        avg = self._shard_avgs.get(self._selected_chest)
        if avg is not None and avg > 0 and shard_qty > avg * 3:
            return (
                f"Shard quantity {shard_qty} is more than 3x the average "
                f"({avg:.0f}). Looks like an error -- not saved."
            )
        return None

    def _write_loot_to_db(
        self,
        loot: list[tuple[int, str]],
        chest_type_override: str | None = None,
    ) -> None:
        """
        Write loot to Supabase.

        Parameters
        ----------
        loot               : list of (qty, item_name) tuples
        chest_type_override: if provided, use this chest type instead of
                             self._selected_chest (used by pattern detection
                             after the bounty override has been resolved).
        """
        chest_type = chest_type_override if chest_type_override else self._selected_chest
        item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, self._item_prices).items()}

        error = self._validate_loot(loot)
        if error:
            self._log_threadsafe(f"! Validation failed: {error}", "red")
            return

        result = db_handler.write_chest_loot(
            chest_type=chest_type,
            loot=loot,
            item_prices=item_prices,
        )

        if not result.success:
            msg = (
                "Not connected to Supabase -- chest data was NOT saved!"
                if result.error == "NOT_CONNECTED"
                else f"Error saving to Supabase: {result.error}"
            )
            self._log_threadsafe(msg, "red")
            _queue(lambda m=msg: _show_modal("Save Error", m))
            return

        for _, item in loot:
            colour = self._tracker.get_item_colour(item)
            self._log_threadsafe(f"  -> {item}", colour)

        self._log_threadsafe(f"[ok] Chest #{result.chest_number} saved to Supabase!", "green")
        if result.chest_revenue > 0:
            self._log_threadsafe(f"Revenue: {self._fmt(result.chest_revenue)}", "green")
            if result.most_expensive_item[1] > 0:
                name, val = result.most_expensive_item
                self._log_threadsafe(f"Top item: {name} ({self._fmt(val)})", "green")
        self._log_threadsafe("=" * 50 + "\n", "green")

        self._session.chest_ids.append(result.chest_id)
        self._session.total_revenue += result.chest_revenue
        self._session.chest_count += 1
        self._mini_avg_revenue = self._session.avg_revenue
        self._last_most_expensive = result.most_expensive_item

        _queue(self._update_mini)
        _queue(self._refresh_db_view)

    # ------------------------------------------------------------------
    # DB view / stats
    # ------------------------------------------------------------------

    def _refresh_db_view(self) -> None:
        if not self._db_connected:
            return
        threading.Thread(target=self._refresh_db_view_worker, daemon=True).start()

    def _refresh_db_view_worker(self) -> None:
        try:
            chest_type = self._viewer.selected_chest() or self._selected_chest
            item_prices = dict(self._all_prices.get(chest_type, self._item_prices))
            item_prices_lower = {k.lower(): v for k, v in item_prices.items()}
            session_only = self._viewer.is_session_mode()
            session_ids = list(self._session.chest_ids)

            total_stats = db_handler.calculate_statistics(chest_type, item_prices_lower)

            if session_only and session_ids:
                session_stats = db_handler.calculate_statistics_for_ids(session_ids, item_prices_lower)
                loot_rows = db_handler.fetch_chests_by_ids(session_ids)
            else:
                session_stats = total_stats
                loot_rows = db_handler.fetch_all_loot(chest_type)

            def _apply(s=session_stats, t=total_stats, l=loot_rows, ip=item_prices_lower):
                self._apply_db_view(s, t, l, ip)

            _queue(_apply)
        except Exception as exc:
            self._log_threadsafe(f"Refresh error: {exc}", "red")

    def _apply_db_view(
        self,
        session_stats: db_handler.Stats,
        total_stats: db_handler.Stats,
        loot_rows: list[dict],
        item_prices: dict[str, float] | None = None,
    ) -> None:
        import pandas as pd

        self._viewer.show_stats(session_stats, total_stats)

        if not loot_rows:
            self._viewer.load_dataframe(pd.DataFrame(), item_prices or self._item_prices)
            self._log(
                f"No chests recorded yet for '{self._selected_chest}' -- ready to track!",
                "gray",
            )
            return

        df = pd.DataFrame(loot_rows)
        pivot = df.pivot_table(
            index=["chest_id", "recorded_at"],
            columns="item_name",
            values="quantity",
            aggfunc="sum",
            fill_value=0,
        ).reset_index()
        pivot.columns.name = None
        pivot.insert(0, "#", range(1, len(pivot) + 1))
        self._viewer.load_dataframe(pivot, item_prices or self._item_prices)

        s, t = session_stats, total_stats
        chests_str = (
            f"{s.total_chests} ({t.total_chests})" if s.total_chests != t.total_chests else str(s.total_chests)
        )
        avg_str = (
            f"{self._fmt(s.avg_revenue_per_chest)} ({self._fmt(t.avg_revenue_per_chest)})"
            if s.total_chests != t.total_chests
            else self._fmt(s.avg_revenue_per_chest)
        )
        self._log(
            f"Loaded {chests_str} chests -- avg {avg_str}, total {self._fmt(s.total_revenue)}",
            "gray",
        )

    def _on_session_toggle(self, session_only: bool) -> None:
        self._refresh_db_view()

    def _on_viewer_chest_selected(self, chest_type: str) -> None:
        self._item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, {}).items()}
        self._refresh_db_view()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def _load_prices(self) -> None:
        chest_prices = prices_config.load_prices(self._selected_chest)
        self._all_prices[self._selected_chest] = chest_prices
        self._item_prices = {k.lower(): v for k, v in chest_prices.items()}
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
        chest_prices = all_prices.get(self._selected_chest, {})
        self._item_prices = {k.lower(): v for k, v in chest_prices.items()}
        self._tracker.set_item_prices(self._item_prices)
        self._log(f"Prices updated: {len(self._item_prices)} items for '{self._selected_chest}'", "green")
        self._refresh_db_view()
        if self._db_connected:
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_to_excel(self) -> None:
        if not self._db_connected:
            _show_modal("Not Connected", "Not connected to Supabase!")
            return

        export_chest = self._viewer.selected_chest() or self._selected_chest
        safe_name = export_chest.replace("'", "").replace(" ", "_")

        def _file_selected(sender, app_data):
            path = app_data.get("file_path_name", "")
            if path:
                threading.Thread(target=self._export_worker, args=(path,), daemon=True).start()
            if dpg.does_item_exist("export_file_dialog"):
                dpg.delete_item("export_file_dialog")

        if dpg.does_item_exist("export_file_dialog"):
            dpg.delete_item("export_file_dialog")

        dpg.add_file_dialog(
            tag="export_file_dialog",
            label="Export to Excel",
            width=700,
            height=450,
            modal=True,
            default_filename=f"{safe_name}_export.xlsx",
            callback=_file_selected,
            cancel_callback=lambda s, a: (
                dpg.delete_item("export_file_dialog") if dpg.does_item_exist("export_file_dialog") else None
            ),
        )
        dpg.add_file_extension(".xlsx", parent="export_file_dialog")

    def _export_worker(self, path: str) -> None:
        try:
            chest_type = self._viewer.selected_chest() or self._selected_chest
            loot_rows = db_handler.fetch_all_loot(chest_type)
            if not loot_rows:
                _queue(lambda: _show_modal("No Data", "No chests recorded yet to export."))
                return
            drop_rates = db_handler.fetch_drop_rates(chest_type)
            prices = self._all_prices.get(chest_type, {})
            pinned_for_chest = prices_config.load_pinned_items(chest_type)
            pinned_lower = [p.lower() for p in pinned_for_chest]

            def _col_sort(name: str) -> tuple[int, float]:
                nl = name.lower()
                pin = next((i for i, p in enumerate(pinned_lower) if p == nl), len(pinned_lower))
                price = -prices.get(name, prices.get(nl, 0.0))
                return (pin, price if pin == len(pinned_lower) else 0.0)

            column_order = sorted(prices.keys(), key=_col_sort)
            saved_to = excel_handler.export_to_excel(
                chest_type,
                loot_rows,
                drop_rates=drop_rates,
                column_order=column_order,
                output_path=path,
            )
            self._log_threadsafe(f"Exported to {saved_to}", "green")
            _queue(lambda p=saved_to: _show_modal("Export Complete", f"Saved to:\n{p}"))
        except Exception as exc:
            msg = f"Export failed: {exc}"
            self._log_threadsafe(msg, "red")
            _queue(lambda m=msg: _show_modal("Export Error", m))

    # ------------------------------------------------------------------
    # Mini window
    # ------------------------------------------------------------------

    def _toggle_mini(self) -> None:
        if self._mini_mode_active:
            self._close_mini()
        else:
            self._open_mini()

    def _open_mini(self) -> None:
        self._mini = MiniWindow(on_close=self._on_mini_closed)
        self._mini_mode_active = True
        self._tracker.set_mini_active(True)
        self._update_mini()

    def _close_mini(self) -> None:
        if self._mini:
            self._mini.close()
            self._mini = None
        self._mini_mode_active = False
        self._tracker.set_mini_active(False)

    def _on_mini_closed(self) -> None:
        self._mini = None
        self._mini_mode_active = False
        self._tracker.set_mini_active(False)

    def _update_mini(self) -> None:
        if self._mini and self._mini.is_alive():
            is_running = self._monitor is not None and self._monitor.is_running
            self._mini.update(
                is_running=is_running,
                most_expensive=self._last_most_expensive,
                avg_revenue=self._mini_avg_revenue,
            )

    # ------------------------------------------------------------------
    # Tray (optional — call _start_tray_icon() from run() to enable)
    # TODO: wire up if system-tray support is desired in a future release.
    # ------------------------------------------------------------------

    def _start_tray_icon(self) -> None:
        if not _TRAY_AVAILABLE or _pystray is None or self._tray_icon is not None:
            return
        menu = _pystray.Menu(
            _pystray.MenuItem("Show Tracker", self._tray_show, default=True),
            _pystray.Menu.SEPARATOR,
            _pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_icon = _pystray.Icon(
            name="ChestTracker",
            icon=_make_tray_icon_image(),
            title="Chest Tracker",
            menu=menu,
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _stop_tray_icon(self) -> None:
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _tray_show(self, icon, item) -> None:
        _queue(self._close_mini)

    def _tray_quit(self, icon, item) -> None:
        _queue(self._on_quit)

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    def _on_quit(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._stop_tray_icon()
        dpg.stop_dearpygui()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        config.save({"log_path": self._log_path, "chest_type": self._selected_chest})

    def _log(self, message: str, colour: str = "black") -> None:
        self._tracker.log(message, colour)

    def _log_threadsafe(self, message: str, colour: str = "black") -> None:
        _queue(lambda m=message, c=colour: self._tracker.log(m, c))

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{value:,.0f}".replace(",", " ")
