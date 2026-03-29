"""
ui/tracker_tab.py
-----------------
The "Live Tracker" tab: file configuration, start/stop controls,
manual trigger, mini-mode toggle, and the scrolled log display.

This class owns only presentation logic.  All heavy lifting (log
monitoring, Excel I/O) is delegated back to the parent app via
callbacks passed at construction.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from datetime import datetime
from typing import Callable

from constants import PRICE_TIER_HIGH, PRICE_TIER_MID


LogCallback = Callable[[str, str], None]


class TrackerTab:
    """
    Builds and manages all widgets inside the Live Tracker tab frame.

    Parameters
    ----------
    parent          : the ttk.Frame that is the tab container
    on_start_stop   : called when the START/STOP button is pressed
    on_manual       : called with chest_type str when manual chest is confirmed
    on_mini_toggle  : called when MINI MODE is pressed
    on_log_browse   : called with the new path when the user picks a log file
    """

    def __init__(
        self,
        parent: ttk.Frame,
        on_start_stop: Callable[[], None],
        on_manual: Callable[[str], None],
        on_mini_toggle: Callable[[], None],
        on_log_browse: Callable[[str], None],
        initial_log_path: str = "",
    ) -> None:
        self._parent = parent
        self._on_start_stop = on_start_stop
        self._on_manual: Callable[[str], None] = on_manual
        self._on_mini_toggle = on_mini_toggle
        self._on_log_browse = on_log_browse

        # Prices cached here so get_item_color can work without app coupling
        self._item_prices: dict[str, float] = {}
        self._chest_types: list[str] = []

        self._build(initial_log_path)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self, log_path: str) -> None:
        # ── File configuration ───────────────────────────────────────
        cfg = tk.LabelFrame(self._parent, text=" File Configuration ", padx=10, pady=10)
        cfg.pack(padx=10, pady=10, fill=tk.X)

        tk.Label(cfg, text="Log file:").grid(row=0, column=0, sticky="w")
        self._log_label = tk.Label(cfg, text=self._short(log_path), fg="blue")
        self._log_label.grid(row=0, column=1, sticky="w", padx=5)
        tk.Button(cfg, text="Browse", command=self._browse_log).grid(row=0, column=2, pady=2)

        tk.Label(cfg, text="Active chest:").grid(row=1, column=0, sticky="w")
        self._sheet_label = tk.Label(cfg, text="Auto-detect from log", fg="purple")
        self._sheet_label.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        # Manual chest selector — stays persistent, no popup dialog
        tk.Label(cfg, text="Manual chest:").grid(row=2, column=0, sticky="w")
        self._manual_combo = ttk.Combobox(cfg, state="readonly", width=28)
        self._manual_combo.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        # ── Status ──────────────────────────────────────────────────
        self._status_label = tk.Label(self._parent, text="Status: Ready", font=("Arial", 12, "bold"), fg="gray")
        self._status_label.pack(pady=5)

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = tk.Frame(self._parent)
        btn_row.pack(pady=10, padx=20, fill=tk.X)

        self._btn_toggle = tk.Button(
            btn_row,
            text="START LISTENING",
            font=("Arial", 10, "bold"),
            command=self._on_start_stop,
            bg="#2ecc71",
            fg="white",
            height=2,
        )
        self._btn_toggle.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        tk.Button(
            btn_row,
            text="MANUAL CHEST",
            font=("Arial", 10, "bold"),
            command=self._manual_btn_pressed,
            bg="#3498db",
            fg="white",
            height=2,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))

        self._btn_mini = tk.Button(
            btn_row,
            text="MINI MODE",
            font=("Arial", 10, "bold"),
            command=self._on_mini_toggle,
            bg="#9b59b6",
            fg="white",
            height=2,
        )
        self._btn_mini.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        # ── Log display ──────────────────────────────────────────────
        self._log_display = scrolledtext.ScrolledText(self._parent, height=20, state="disabled", font=("Consolas", 10))
        self._log_display.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        self._init_colour_tags()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def log(self, message: str, colour: str = "black") -> None:
        """Append a timestamped, coloured line to the log display."""
        self._log_display.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_display.insert(tk.END, f"[{ts}] ")
        self._log_display.insert(tk.END, f"{message}\n", colour)
        self._log_display.see(tk.END)
        self._log_display.config(state="disabled")

    def set_status(self, text: str, colour: str = "gray") -> None:
        self._status_label.config(text=f"Status: {text}", fg=colour)

    def set_listening(self, listening: bool) -> None:
        """Flip the START/STOP button appearance."""
        if listening:
            self._btn_toggle.config(text="STOP LISTENING", bg="#e74c3c")
        else:
            self._btn_toggle.config(text="START LISTENING", bg="#2ecc71")

    def set_mini_active(self, active: bool) -> None:
        if active:
            self._btn_mini.config(text="CLOSE MINI", bg="#e74c3c")
        else:
            self._btn_mini.config(text="MINI MODE", bg="#9b59b6")

    def set_sheet_label(self, name: str) -> None:
        self._sheet_label.config(text=name or "Auto")

    def set_log_path_label(self, path: str) -> None:
        self._log_label.config(text=self._short(path))

    def set_chest_types(self, chest_types: list[str]) -> None:
        """Update the chest type list and populate the manual combo."""
        self._chest_types = chest_types
        self._manual_combo["values"] = chest_types
        if chest_types and not self._manual_combo.get():
            self._manual_combo.set(chest_types[0])

    def set_item_prices(self, prices: dict[str, float]) -> None:
        """Update the price lookup used for log-line colouring."""
        self._item_prices = prices

    def get_item_colour(self, item_name: str) -> str:
        """Map an item name to a log-display colour based on its price."""
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
    # Private helpers
    # ------------------------------------------------------------------

    def _init_colour_tags(self) -> None:
        colours = {
            "black": "black",
            "blue": "blue",
            "green": "green",
            "red": "red",
            "orange": "orange",
            "gray": "gray",
            "light_gray": "#AAAAAA",
            "dark_red": "#8B0000",
            "purple": "purple",
        }
        for tag, fg in colours.items():
            self._log_display.tag_config(tag, foreground=fg)

    @staticmethod
    def _short(path: str) -> str:
        return os.path.basename(path) if path else "Not selected"

    def _manual_btn_pressed(self) -> None:
        """Fire on_manual with the currently selected chest from the inline combo."""
        selected = self._manual_combo.get()
        if not selected:
            from tkinter import messagebox

            messagebox.showwarning("No Chest Selected", "Select a chest type in the Manual chest dropdown first.")
            return
        self._on_manual(selected)

    def _browse_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Log File",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if path:
            self._log_label.config(text=self._short(path))
            self._on_log_browse(path)
