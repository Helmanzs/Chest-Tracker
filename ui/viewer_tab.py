"""
ui/viewer_tab.py
----------------
The "Excel Data" tab.

Features
--------
- Chest type selector (dropdown) — shows data for one chest at a time
- Checkbox to toggle between current session and all-time data
- Export to Excel button (file picker dialog)
- Statistics panel shows session stats with total-average in brackets
- Treeview columns sorted by item price, pinned items first
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

import pandas as pd

import db_handler

PINNED_COLUMNS = ["#", "chest_id", "recorded_at", "Shard", "Energy Fragment"]


class ViewerTab:
    """
    Parameters
    ----------
    parent            : ttk.Frame tab container
    chest_types       : ordered list of chest display names
    on_refresh        : called when Refresh is pressed
    on_reload_prices  : called when Reload Prices is pressed
    on_export         : called when Export to Excel is pressed
    on_session_toggle : called with bool (True = session only)
    on_chest_selected : called with chest_type str when selector changes
    """

    def __init__(
        self,
        parent: ttk.Frame,
        chest_types: list[str],
        on_refresh: Callable[[], None],
        on_reload_prices: Callable[[], None],
        on_export: Callable[[], None],
        on_session_toggle: Callable[[bool], None],
        on_chest_selected: Callable[[str], None],
    ) -> None:
        self._parent = parent
        self._chest_types = chest_types
        self._on_refresh = on_refresh
        self._on_reload_prices = on_reload_prices
        self._on_export = on_export
        self._on_session_toggle = on_session_toggle
        self._on_chest_selected = on_chest_selected
        self._session_var = tk.BooleanVar(value=False)
        self._chest_var = tk.StringVar(value=chest_types[0] if chest_types else "")
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # ── Top row: chest selector + session checkbox ────────────────
        top_row = tk.Frame(self._parent)
        top_row.pack(fill=tk.X, padx=10, pady=(8, 2))

        tk.Label(top_row, text="Chest:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        self._chest_combo = ttk.Combobox(
            top_row,
            textvariable=self._chest_var,
            values=self._chest_types,
            state="readonly",
            width=28,
        )
        self._chest_combo.pack(side=tk.LEFT, padx=(0, 12))
        self._chest_combo.bind("<<ComboboxSelected>>", self._on_combo)

        tk.Checkbutton(
            top_row,
            text="Show current session only",
            variable=self._session_var,
            command=self._on_checkbox,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(0, 0))

        # ── Button row ───────────────────────────────────────────────
        btn_row = tk.Frame(self._parent)
        btn_row.pack(fill=tk.X, padx=10, pady=(2, 5))

        tk.Button(btn_row, text="Refresh Data", command=self._on_refresh).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(btn_row, text="Reload Prices", command=self._on_reload_prices).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(
            btn_row,
            text="Export to Excel",
            command=self._on_export,
            bg="#27ae60",
            fg="white",
            relief=tk.FLAT,
            padx=8,
        ).pack(side=tk.LEFT, padx=(0, 5))

        # ── Statistics panel ─────────────────────────────────────────
        stats = tk.LabelFrame(self._parent, text=" Statistics ", padx=10, pady=8)
        stats.pack(fill=tk.X, padx=10, pady=5)
        grid = tk.Frame(stats)
        grid.pack(fill=tk.X)

        tk.Label(grid, text="Chests:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", padx=5)
        self._total_chests = tk.Label(grid, text="0", font=("Arial", 10), fg="blue")
        self._total_chests.grid(row=0, column=1, sticky="w", padx=5)

        tk.Label(grid, text="Revenue/Chest:", font=("Arial", 10, "bold")).grid(row=0, column=2, sticky="w", padx=20)
        self._rev_per_chest = tk.Label(grid, text="N/A", font=("Arial", 10), fg="green")
        self._rev_per_chest.grid(row=0, column=3, sticky="w", padx=5)

        tk.Label(grid, text="Total Revenue:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", padx=5)
        self._total_rev = tk.Label(grid, text="N/A", font=("Arial", 10), fg="green")
        self._total_rev.grid(row=1, column=1, sticky="w", padx=5)

        # ── Treeview ─────────────────────────────────────────────────
        tree_frame = tk.Frame(self._parent)
        tree_frame.pack(expand=True, fill="both", padx=10, pady=10)

        self._tree = ttk.Treeview(tree_frame, show="headings")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def selected_chest(self) -> str:
        return self._chest_var.get()

    def set_selected_chest(self, chest_type: str) -> None:
        """Update the dropdown to reflect auto-detected chest."""
        if chest_type in self._chest_types:
            self._chest_var.set(chest_type)

    def set_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        self._chest_combo["values"] = chest_types
        if chest_types and not self._chest_var.get():
            self._chest_var.set(chest_types[0])

    def load_dataframe(self, df: pd.DataFrame, item_prices: dict[str, float] | None = None) -> None:
        self._tree.delete(*self._tree.get_children())
        if df.empty:
            self._tree["columns"] = []
            return

        cols = self._sort_columns(list(df.columns), item_prices or {})
        df = df[cols]

        self._tree["columns"] = cols
        for col in cols:
            width = max(len(str(col)) * 9, 80)
            self._tree.heading(col, text=col)
            self._tree.column(col, width=width, anchor="center", minwidth=60)

        for _, row in df.iterrows():
            self._tree.insert("", "end", values=list(row))

    def show_stats(
        self,
        session_stats: db_handler.Stats,
        total_stats: db_handler.Stats | None = None,
    ) -> None:
        s = session_stats
        t = total_stats

        if s.total_chests == 0:
            self._total_chests.config(text="0" + (f" ({t.total_chests})" if t else ""))
            self._reset_revenue_labels(t)
            return

        chests_text = str(s.total_chests)
        if t and t.total_chests != s.total_chests:
            chests_text += f" ({t.total_chests})"
        self._total_chests.config(text=chests_text)

        avg_text = self._fmt(s.avg_revenue_per_chest)
        if t and t.total_chests != s.total_chests:
            avg_text += f" ({self._fmt(t.avg_revenue_per_chest)})"
        self._rev_per_chest.config(text=avg_text)

        total_text = self._fmt(s.total_revenue)
        if t and t.total_chests != s.total_chests:
            total_text += f" ({self._fmt(t.total_revenue)})"
        self._total_rev.config(text=total_text)

    def show_stats_error(self) -> None:
        self._total_chests.config(text="Error")
        self._rev_per_chest.config(text="Error")
        self._total_rev.config(text="Error")

    def is_session_mode(self) -> bool:
        return self._session_var.get()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_combo(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        self._on_chest_selected(self._chest_var.get())

    def _on_checkbox(self) -> None:
        self._on_session_toggle(self._session_var.get())

    def _reset_revenue_labels(self, total: db_handler.Stats | None = None) -> None:
        if total and total.total_chests > 0:
            self._rev_per_chest.config(text=f"N/A ({self._fmt(total.avg_revenue_per_chest)})")
            self._total_rev.config(text=f"N/A ({self._fmt(total.total_revenue)})")
        else:
            self._rev_per_chest.config(text="N/A")
            self._total_rev.config(text="N/A")

    @staticmethod
    def _sort_columns(cols: list[str], item_prices: dict[str, float]) -> list[str]:
        pinned = [c for c in PINNED_COLUMNS if c in cols]
        unpinned = [c for c in cols if c not in pinned]
        unpinned.sort(key=lambda c: -item_prices.get(c.lower(), 0.0))
        return pinned + unpinned

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{int(value):,}".replace(",", " ")
