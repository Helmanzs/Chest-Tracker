"""
app.py
------
Central application class.  Owns all state and wires the UI tabs,
log monitor, db handler, and Excel price/export handler together.

Data flow
---------
  Chest loot  →  db_handler   (Supabase)
  Item prices →  config.load_prices()  (prices_config.txt)
  Export      →  excel_handler.export_to_excel()   (on demand)

System tray
-----------
Requires:  pip install pystray pillow supabase
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import config
import db_handler
import excel_handler
from dataclasses import dataclass, field
from constants import (
    CHEST_DATA_SHEETS,
    CHEST_DISPLAY_NAMES,
    CHEST_COLORS,
    DEFAULT_CHEST_TYPE,
)
from log_monitor import LogMonitor
from ui.mini_window import MiniWindow
from ui.tracker_tab import TrackerTab
from ui.prices_tab import PricesTab
import updater

APP_VERSION = "1.0.6"
from ui.viewer_tab import ViewerTab


@dataclass
class _Session:
    """Tracks stats for the current listening session."""

    chest_ids: list[int] = field(default_factory=list)
    total_revenue: float = 0.0
    chest_count: int = 0

    @property
    def avg_revenue(self) -> float:
        return self.total_revenue / self.chest_count if self.chest_count else 0.0


try:
    import pystray as pystray
    from PIL import Image as _PIL_Image, ImageDraw as _PIL_ImageDraw

    _TRAY_AVAILABLE = True
except ImportError:
    pystray = None  # type: ignore[assignment]
    _PIL_Image = None  # type: ignore[assignment]
    _PIL_ImageDraw = None  # type: ignore[assignment]
    _TRAY_AVAILABLE = False


def _make_tray_icon_image(size: int = 64) -> "_PIL_Image.Image":  # type: ignore[name-defined]
    img = _PIL_Image.new("RGBA", (size, size), (0, 0, 0, 0))  # type: ignore[union-attr]
    draw = _PIL_ImageDraw.Draw(img)  # type: ignore[union-attr]
    draw.ellipse([4, 4, size - 4, size - 4], fill="#2ecc71", outline="#27ae60", width=3)
    return img


class App:
    """Root application controller."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._root.title(f"Multi-Chest Tracker v{APP_VERSION}")
        self._root.geometry("900x750")
        self._root.protocol("WM_DELETE_WINDOW", self._on_quit)

        # ── Persisted settings ───────────────────────────────────────
        self._log_path: str = config.load("log_path")
        self._selected_chest: str = config.load("chest_type") or DEFAULT_CHEST_TYPE
        self._sheet_name: str = CHEST_DATA_SHEETS.get(self._selected_chest, "")

        # ── Runtime state ────────────────────────────────────────────
        self._item_prices: dict[str, float] = {}  # prices for current chest
        self._all_prices: dict[str, dict[str, float]] = {}  # prices for all chests
        self._shard_avgs: dict[str, float] = {}  # {chest_type: avg shard qty}
        self._db_connected: bool = False

        self._last_most_expensive: tuple[str, float] = ("-", 0.0)
        self._avg_revenue: float = 0.0  # used by viewer tab
        self._mini_avg_revenue: float = 0.0  # always tracks active chest, shown in mini

        self._monitor: LogMonitor | None = None
        self._tray_icon: object = None  # pystray.Icon when active
        self._session: _Session = _Session()

        # ── UI ───────────────────────────────────────────────────────
        notebook = ttk.Notebook(root)
        tab_tracker = ttk.Frame(notebook)
        tab_viewer = ttk.Frame(notebook)
        tab_prices = ttk.Frame(notebook)
        notebook.add(tab_tracker, text=" Live Tracker ")
        notebook.add(tab_viewer, text=" Excel Data ")
        notebook.add(tab_prices, text=" Prices ")
        notebook.pack(expand=1, fill="both")

        self._tracker = TrackerTab(
            parent=tab_tracker,
            on_start_stop=self._toggle_service,
            on_manual=self._manual_chest_trigger,
            on_mini_toggle=self._toggle_mini,
            on_log_browse=self._on_log_browse,
            initial_log_path=self._log_path,
        )

        self._viewer = ViewerTab(
            parent=tab_viewer,
            chest_types=list(CHEST_DATA_SHEETS.keys()),
            on_refresh=self._refresh_db_view,
            on_reload_prices=self._reload_prices,
            on_export=self._export_to_excel,
            on_session_toggle=self._on_session_toggle,
            on_chest_selected=self._on_viewer_chest_selected,
            initial_chest=self._selected_chest,
        )

        self._prices_tab = PricesTab(
            parent=tab_prices,
            chest_types=list(CHEST_DATA_SHEETS.keys()),
            on_prices_changed=self._on_prices_changed,
        )

        self._mini: MiniWindow | None = None

        # Defer startup tasks until after event loop starts
        self._root.after(0, self._startup)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _startup(self) -> None:
        """Connect to DB and load prices after UI is fully built."""
        threading.Thread(target=self._check_for_update, daemon=True).start()
        self._connect_db()
        self._load_all_prices_startup()
        self._refresh_db_view()
        # Pass chest types to tracker for the manual chest dialog
        self._tracker.set_chest_types(list(CHEST_DATA_SHEETS.keys()))
        if self._db_connected:
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    def _load_all_prices_startup(self) -> None:
        """Load prices for every chest type and log a summary for each."""
        from config import load_all_prices as _load_all

        self._all_prices = _load_all()
        for chest_type in CHEST_DATA_SHEETS:
            prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, {}).items()}
            count = len(prices)
            if count:
                self._log(f"Loaded {count} prices for '{chest_type}'", "green")
            else:
                self._log(f"No prices set for '{chest_type}' — set them in the Prices tab.", "orange")
        # Keep item_prices for the viewer-selected chest (defaults to first)
        viewer_chest = self._viewer.selected_chest() or self._selected_chest
        self._item_prices = {k.lower(): v for k, v in self._all_prices.get(viewer_chest, {}).items()}
        self._tracker.set_item_prices(self._item_prices)

    def _startup_drop_rates(self) -> None:
        """Fetch drop rates, avg qtys, and per-chest stats for all chest types."""
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

        def _log_stats() -> None:
            from constants import CHEST_DISPLAY_NAMES

            for ct, st in all_stats.items():
                short = CHEST_DISPLAY_NAMES.get(ct, ct.replace("'s Chest", "").replace(" Chest", "").strip())
                if st.total_chests > 0:
                    self._log(
                        f"{short}: {st.total_chests} chests — avg {self._fmt(st.avg_revenue_per_chest)}",
                        "gray",
                    )
            self._prices_tab.apply_drop_rates(all_rates, all_stats, all_avgs)

        self._root.after(0, _log_stats)

    def _check_for_update(self) -> None:
        """Background thread: check GitHub for a newer release."""
        result = updater.check_for_update(APP_VERSION)
        if result.error:
            self._log_threadsafe(f"[updater] {result.error}", "gray")
            return
        if not result.update_available:
            self._log_threadsafe(f"App is up to date (v{APP_VERSION})", "gray")
            return
        # Newer version available — prompt on main thread
        self._root.after(0, lambda: self._prompt_update(result))

    def _prompt_update(self, result: updater.UpdateResult) -> None:
        from tkinter import messagebox

        lines = [
            "A new version is available!",
            "",
            f"  Current:  v{result.current_version}",
            f"  Latest:   {result.latest_version}",
        ]
        if result.release_notes:
            lines += ["", "Changes:", result.release_notes, ""]
        lines.append("Download and restart now?")
        msg = "\n".join(lines)
        if messagebox.askyesno("Update Available", msg):
            self._log("Downloading update...", "blue")
            updater.download_and_replace(
                result,
                on_progress=lambda m: self._log_threadsafe(m, "blue"),
                on_complete=self._on_update_complete,
            )

    def _on_update_complete(self, success: bool, message: str) -> None:
        self._log_threadsafe(message, "green" if success else "red")
        if success:
            from tkinter import messagebox

            self._root.after(
                0,
                lambda: messagebox.showinfo(
                    "Update Ready",
                    f"{message}\n\nClose and reopen the app to use the new version.",
                ),
            )

    def _connect_db(self) -> None:
        url = config.load("supabase_url")
        key = config.load("supabase_key")
        self._db_connected = db_handler.init(url, key)
        if self._db_connected:
            self._log("Connected to Supabase ✓", "green")
        else:
            self._log(
                "Supabase not connected — set supabase_url and supabase_key in tracker_config.txt",
                "red",
            )

    # ------------------------------------------------------------------
    # File browsing callbacks
    # ------------------------------------------------------------------

    def _on_log_browse(self, path: str) -> None:
        self._log_path = path
        self._log(f"Log file selected: {path}", "blue")
        self._save_config()

    # ------------------------------------------------------------------
    # Chest type selection
    # ------------------------------------------------------------------

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
            messagebox.showwarning("Log File Missing", "Please select a valid log file first!")
            return
        if not self._db_connected:
            messagebox.showwarning(
                "Not Connected",
                "Not connected to Supabase.\nCheck supabase_url and supabase_key in tracker_config.txt",
            )
            return

        if not self._item_prices:
            self._load_prices()

        self._monitor = LogMonitor(
            log_path=self._log_path,
            chest_types=CHEST_DATA_SHEETS,
            selected_chest=self._selected_chest,
            on_chest_detected=self._on_chest_detected,
            on_loot_item=self._on_loot_item,
            on_log=self._log_threadsafe,
            on_timeout=self._on_loot_timeout,
            on_pattern_chest=self._on_pattern_chest_detected,
        )
        self._monitor.start()

        self._session = _Session()  # reset on each start
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
            messagebox.showwarning("Not Connected", "Not connected to Supabase!")
            return

        if not self._item_prices or self._selected_chest != chest_type:
            self._selected_chest = chest_type
            self._sheet_name = CHEST_DATA_SHEETS.get(chest_type, "")
            self._load_prices()
            self._tracker.set_sheet_label(self._sheet_name)

        if self._monitor is None:
            self._monitor = LogMonitor(
                log_path=self._log_path or "",
                chest_types=CHEST_DATA_SHEETS,
                selected_chest=chest_type,
                on_chest_detected=self._on_chest_detected,
                on_loot_item=self._on_loot_item,
                on_log=self._log_threadsafe,
                on_timeout=self._on_loot_timeout,
                on_pattern_chest=self._on_pattern_chest_detected,
            )

        self._on_chest_detected(chest_type)
        self._log("Manual chest tracking started. Waiting for timeout or next chest...", "purple")

        if not self._monitor.is_running:
            threading.Thread(target=self._manual_timeout_loop, daemon=True).start()

    def _manual_timeout_loop(self) -> None:
        import time
        from constants import LOOT_TIMEOUT

        assert self._monitor is not None
        while self._monitor._awaiting_loot:  # noqa: SLF001
            loot = self._monitor.captured_loot
            last = self._monitor._last_loot_time  # noqa: SLF001
            ts = self._monitor._target_timestamp  # noqa: SLF001
            if last and ts and loot:
                if time.time() - last >= LOOT_TIMEOUT:
                    self._log(f"Loot collection timeout ({LOOT_TIMEOUT}s). Saving...", "orange")
                    self._on_loot_timeout()
                    break
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # LogMonitor callbacks
    # ------------------------------------------------------------------

    def _on_chest_detected(self, chest_name: str) -> None:
        assert self._monitor is not None
        pending = self._monitor.finalize()
        if pending:
            self._log("Saving previous chest data...", "orange")
            self._write_loot_to_db(pending)

        # Auto-switch chest type from log and sync viewer dropdown
        if chest_name != self._selected_chest:
            self._selected_chest = chest_name
            self._sheet_name = CHEST_DATA_SHEETS.get(chest_name, "")
            self._item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_name, {}).items()}
            self._tracker.set_item_prices(self._item_prices)
            self._tracker.set_sheet_label(self._sheet_name)
            self._root.after(0, lambda ct=chest_name: self._viewer.set_selected_chest(ct))
            # Reset mini avg when switching chest types
            self._mini_avg_revenue = 0.0
            self._session = _Session()
            self._save_config()

        self._monitor.start_new_chest(chest_name)
        self._log("\n" + "=" * 50, "blue")
        self._log(f"[!] {chest_name.upper()} DETECTED! Waiting for loot...", "blue")
        self._log("=" * 50, "blue")
        self._update_mini()

    def _on_pattern_chest_detected(self, chest_name: str, loot: list[tuple[int, str]]) -> None:
        """Called when a loot batch matches a pattern-detected chest signature."""
        self._log_threadsafe(f"[!] {chest_name.upper()} DETECTED (pattern match)!", "blue")
        # Switch context to this chest type
        if chest_name != self._selected_chest:
            self._selected_chest = chest_name
            self._sheet_name = CHEST_DATA_SHEETS.get(chest_name, chest_name)
            self._item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_name, {}).items()}
            self._tracker.set_item_prices(self._item_prices)
            self._root.after(0, lambda ct=chest_name: self._viewer.set_selected_chest(ct))
            self._mini_avg_revenue = 0.0
            self._session = _Session()
        # Write directly — loot is already complete from the free buffer
        threading.Thread(
            target=self._write_loot_to_db,
            args=(loot,),
            daemon=True,
        ).start()

    def _on_loot_item(self, qty: int, item: str) -> None:
        colour = self._tracker.get_item_colour(item)
        self._log_threadsafe(f" + Found: {qty}x {item}", colour)
        self._update_mini()

    def _on_loot_timeout(self) -> None:
        assert self._monitor is not None
        loot = self._monitor.finalize()
        if not loot:
            self._log_threadsafe("No loot to save.", "gray")
            return
        self._log_threadsafe(f"Finalizing {len(loot)} items...", "blue")
        threading.Thread(target=self._write_loot_to_db, args=(loot,), daemon=True).start()

    # ------------------------------------------------------------------
    # DB writing
    # ------------------------------------------------------------------

    _SKIP_SILENT = "__skip_silent__"

    def _validate_loot(self, loot: list[tuple[int, str]]) -> str | None:
        """
        Return an error message if loot looks like a falsified/error report,
        None if it passes, or _SKIP_SILENT to discard silently.

        By the time this runs, direct boss drops have already been filtered
        by log_monitor (pending chest confirmation). So here we only check:
        - Shard quantity must be > 0
        - Shard quantity must be <= avg_shard_qty * 2  (if avg is known)
        """
        shard_qty = next(
            (qty for qty, item in loot if item.strip().lower() == "shard"),
            None,
        )

        if shard_qty is None or shard_qty == 0:
            return "Shard quantity is 0 — chest data looks incomplete. Not saved."

        avg = self._shard_avgs.get(self._selected_chest)
        if avg is not None and avg > 0 and shard_qty > avg * 2:
            return (
                f"Shard quantity {shard_qty} is more than 2x the average "
                f"({avg:.0f}). Looks like an error — not saved."
            )
        return None

    def _write_loot_to_db(self, loot: list[tuple[int, str]]) -> None:
        # Validate before writing
        error = self._validate_loot(loot)
        if error == self._SKIP_SILENT:
            self._log_threadsafe("Skipped direct boss drop (no Shard — not a chest opening).", "gray")
            return
        if error:
            self._log_threadsafe(f"⚠ Validation failed: {error}", "red")
            return

        result = db_handler.write_chest_loot(
            chest_type=self._selected_chest,
            loot=loot,
            item_prices=self._item_prices,
        )

        if not result.success:
            if result.error == "NOT_CONNECTED":
                msg = "Not connected to Supabase — chest data was NOT saved!"
            else:
                msg = f"Error saving to Supabase: {result.error}"
            self._log_threadsafe(msg, "red")
            self._root.after(0, lambda m=msg: messagebox.showerror("Save Error", m))
            return

        for _, item in loot:
            colour = self._tracker.get_item_colour(item)
            self._log_threadsafe(f"  → {item}", colour)

        self._log_threadsafe(f"✓ Chest #{result.chest_number} saved to Supabase!", "green")

        if result.chest_revenue > 0:
            self._log_threadsafe(f"Revenue: {self._fmt(result.chest_revenue)}", "green")
            if result.most_expensive_item[1] > 0:
                name, val = result.most_expensive_item
                self._log_threadsafe(f"Top item: {name} ({self._fmt(val)})", "green")

        self._log_threadsafe("=" * 50 + "\n", "green")
        # Update session immediately so mini window shows live data
        self._session.chest_ids.append(result.chest_id)
        self._session.total_revenue += result.chest_revenue
        self._session.chest_count += 1
        # Update mini avg from session — always reflects active chest
        self._mini_avg_revenue = self._session.avg_revenue
        self._avg_revenue = self._session.avg_revenue

        self._last_most_expensive = result.most_expensive_item
        self._root.after(0, self._update_mini)
        self._root.after(200, self._refresh_db_view)

    # ------------------------------------------------------------------
    # DB view / statistics
    # ------------------------------------------------------------------

    def _refresh_db_view(self) -> None:
        if not self._db_connected:
            self._log("Not connected to Supabase", "red")
            return
        threading.Thread(target=self._refresh_db_view_worker, daemon=True).start()

    def _refresh_db_view_worker(self) -> None:
        try:
            # Snapshot mutable state before any async work
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

            self._avg_revenue = session_stats.avg_revenue_per_chest
            self._root.after(
                0,
                lambda s=session_stats, t=total_stats, l=loot_rows, ip=item_prices_lower: self._apply_db_view(
                    s, t, l, ip
                ),
            )
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

        # Show session stats with total in brackets when they differ
        self._viewer.show_stats(session_stats, total_stats)

        if not loot_rows:
            self._viewer.load_dataframe(pd.DataFrame(), item_prices or self._item_prices)
            self._log(
                f"No chests recorded yet for '{self._selected_chest}' — ready to track!",
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

        # Log line: "Loaded 11 (124) chests — avg 1 700 304 (1 258 304), total 154 789 712"
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
            f"Loaded {chests_str} chests — avg {avg_str}, total {self._fmt(s.total_revenue)}",
            "gray",
        )

    def _on_session_toggle(self, session_only: bool) -> None:
        self._refresh_db_view()

    def _on_viewer_chest_selected(self, chest_type: str) -> None:
        """Called when the user picks a chest in the Excel Data tab."""
        self._item_prices = {k.lower(): v for k, v in self._all_prices.get(chest_type, {}).items()}
        self._refresh_db_view()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def _load_prices(self) -> None:
        """Load prices for current chest type from prices_config.txt."""
        chest_prices = config.load_prices(self._selected_chest)
        self._all_prices[self._selected_chest] = chest_prices
        self._item_prices = {k.lower(): v for k, v in chest_prices.items()}
        self._tracker.set_item_prices(self._item_prices)
        if self._item_prices:
            self._log(f"Loaded {len(self._item_prices)} prices for '{self._selected_chest}'", "green")
        else:
            self._log(
                f"No prices set for '{self._selected_chest}' — set them in the Prices tab.",
                "orange",
            )

    def _reload_prices(self) -> None:
        self._load_prices()
        self._refresh_db_view()

    def _on_prices_changed(self, all_prices: dict[str, dict[str, float]]) -> None:
        """Called by PricesTab after any save — hot-reload prices for all chests."""
        self._all_prices = all_prices
        chest_prices = all_prices.get(self._selected_chest, {})
        self._item_prices = {k.lower(): v for k, v in chest_prices.items()}
        self._tracker.set_item_prices(self._item_prices)
        self._log(
            f"Prices updated: {len(self._item_prices)} items for '{self._selected_chest}'",
            "green",
        )
        self._refresh_db_view()
        if self._db_connected:
            threading.Thread(target=self._startup_drop_rates, daemon=True).start()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_to_excel(self) -> None:
        if not self._db_connected:
            messagebox.showwarning("Not Connected", "Not connected to Supabase!")
            return

        export_chest = self._viewer.selected_chest() or self._selected_chest
        safe_name = export_chest.replace("'", "").replace(" ", "_")
        path = filedialog.asksaveasfilename(
            title="Export to Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
            initialfile=f"{safe_name}_export.xlsx",
        )
        if not path:
            return

        self._log("Exporting data...", "blue")
        threading.Thread(target=self._export_worker, args=(path,), daemon=True).start()

    def _export_worker(self, path: str) -> None:
        try:
            chest_type = self._viewer.selected_chest() or self._selected_chest
            loot_rows = db_handler.fetch_all_loot(chest_type)
            if not loot_rows:
                self._root.after(
                    0,
                    lambda: messagebox.showwarning("No Data", "No chests recorded yet to export."),
                )
                return
            drop_rates = db_handler.fetch_drop_rates(chest_type)
            # Build column order: pinned items first, then by price desc
            prices = self._all_prices.get(chest_type, {})
            pinned_for_chest = config.load_pinned_items(chest_type)
            pinned_lower = [p.lower() for p in pinned_for_chest]

            def _col_sort(name: str) -> tuple[int, float]:
                nl = name.lower()
                pin = next((i for i, p in enumerate(pinned_lower) if p == nl), len(pinned_lower))
                price = -prices.get(name, prices.get(name.lower(), 0.0))
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
            self._root.after(0, lambda: messagebox.showinfo("Export Complete", f"Saved to:\n{saved_to}"))
        except Exception as exc:
            msg = f"Export failed: {exc}"
            self._log_threadsafe(msg, "red")
            self._root.after(0, lambda m=msg: messagebox.showerror("Export Error", m))

    def _toggle_mini(self) -> None:
        if self._mini is not None:
            self._close_mini_and_restore()
        else:
            self._mini = MiniWindow(root=self._root, on_close=self._on_mini_closed)
            self._tracker.set_mini_active(True)
            self._update_mini()
            self._root.withdraw()
            self._start_tray_icon()

    def _on_mini_closed(self) -> None:
        self._mini = None
        self._stop_tray_icon()
        self._tracker.set_mini_active(False)
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _close_mini_and_restore(self) -> None:
        if self._mini is not None:
            self._mini._on_close = lambda: None  # type: ignore[attr-defined]
            self._mini.close()
            self._mini = None
        self._stop_tray_icon()
        self._tracker.set_mini_active(False)
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _start_tray_icon(self) -> None:
        if not _TRAY_AVAILABLE:
            return
        if self._tray_icon is not None:
            return
        assert pystray is not None
        menu = pystray.Menu(
            pystray.MenuItem("Show Tracker", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_icon = pystray.Icon(
            name="ChestTracker",
            icon=_make_tray_icon_image(),
            title="Chest Tracker",
            menu=menu,
        )
        assert self._tray_icon is not None
        threading.Thread(target=self._tray_icon.run, daemon=True).start()  # type: ignore[union-attr]

    def _stop_tray_icon(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()  # type: ignore[union-attr]
            except Exception:
                pass
            self._tray_icon = None

    def _tray_show(self, icon: object, item: object) -> None:
        self._root.after(0, self._close_mini_and_restore)

    def _tray_quit(self, icon: object, item: object) -> None:
        self._root.after(0, self._on_quit)

    def _update_mini(self) -> None:
        if self._mini is None:
            return
        is_running = self._monitor is not None and self._monitor.is_running
        mini_avg = self._mini_avg_revenue
        self._root.after(
            0,
            lambda: (
                self._mini.update(  # type: ignore[union-attr]
                    is_running=is_running,
                    most_expensive=self._last_most_expensive,
                    avg_revenue=mini_avg,
                )
                if self._mini
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    def _on_quit(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._stop_tray_icon()
        self._root.destroy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        config.save(
            {
                "log_path": self._log_path,
                "chest_type": self._selected_chest,
            }
        )

    def _log(self, message: str, colour: str = "black") -> None:
        self._tracker.log(message, colour)

    def _log_threadsafe(self, message: str, colour: str = "black") -> None:
        self._root.after(0, lambda: self._tracker.log(message, colour))

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{value:,.0f}".replace(",", " ")
