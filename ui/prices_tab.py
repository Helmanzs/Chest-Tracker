"""
ui/prices_tab.py
----------------
Prices tab with per-card scrollable panels, drop chance column,
and grouping: pinned → chest-specific → shared (same-price tier at bottom).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable
from collections import Counter

import config
import db_handler

CHEST_COLOURS: dict[str, tuple[str, str]] = {
    "Razador's Chest": ("#c0392b", "white"),
    "Nemere's Chest": ("#aed6f1", "#1a252f"),
    "Jotun Thrym's Chest": ("#a9dfbf", "#1a252f"),
    "Hellgates Chest": ("#1a1a1a", "white"),
}
_DEFAULT_HEADER = ("#555555", "white")

_SHORT_NAMES: dict[str, str] = {
    "Hellgates Chest": "Blue Death",
}

PINNED_ITEMS: list[str] = ["Shard", "Energy Fragment"]
_PINNED_LOWER = [p.lower() for p in PINNED_ITEMS]

_ROW_BG = "white"
_ROW_ALT_BG = "#f7f7f7"
_BORDER = "#dcdcdc"
_CARD_W = 380
_CARD_ROWS_H = 480
_FG_ZERO = "#b8b8b8"
_FG_NORMAL = "#1a1a1a"
_FG_CHANCE = "#7f8c8d"


def parse_price(raw: str) -> float:
    s = raw.strip().replace(" ", "").replace(",", "").lower()
    if not s:
        return 0.0
    multiplier = 1
    if s.endswith("kkk"):
        multiplier = 1_000_000_000
        s = s[:-3]
    elif s.endswith("kk"):
        multiplier = 1_000_000
        s = s[:-2]
    elif s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    return float(s) * multiplier


def fmt_price(price: float) -> str:
    if price != int(price):
        return f"{price:,.2f}".replace(",", " ")
    return f"{int(price):,}".replace(",", " ")


class PricesTab:
    def __init__(
        self,
        parent: ttk.Frame,
        chest_types: list[str],
        on_prices_changed: Callable[[dict[str, dict[str, float]]], None],
    ) -> None:
        self._parent = parent
        self._chest_types = chest_types
        self._on_prices_changed = on_prices_changed

        self._vars: dict[str, dict[str, tk.StringVar]] = {}
        self._shared_items: set[str] = set()
        self._widgets: dict[str, dict[str, tuple[tk.Label, tk.Label, tk.Entry]]] = {}
        # {chest_type: {item_name: drop_pct}}
        self._drop_rates: dict[str, dict[str, float]] = {}
        self._scroll_refresh_id: str | None = None

        self._build()
        self._load_all()

    # ------------------------------------------------------------------
    # Scaffold
    # ------------------------------------------------------------------

    def _build(self) -> None:
        toolbar = tk.Frame(self._parent, bg="#f0f0f0", pady=6)
        toolbar.pack(fill=tk.X)

        tk.Button(
            toolbar,
            text="💾  Save All Prices",
            font=("Arial", 10, "bold"),
            bg="#2ecc71",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self._save_all,
        ).pack(side=tk.LEFT, padx=(10, 6))

        tk.Button(
            toolbar,
            text="➕  Add Item",
            font=("Arial", 10),
            bg="#3498db",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=lambda: self._add_item_dialog(),
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            toolbar,
            text="🔄  Reload",
            font=("Arial", 10),
            bg="#95a5a6",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self._reload,
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            toolbar,
            text="📊  Refresh Drop Rates",
            font=("Arial", 10),
            bg="#8e44ad",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self._refresh_drop_rates,
        ).pack(side=tk.LEFT, padx=(0, 6))

        self._sync_label = tk.Label(toolbar, text="", font=("Arial", 8), fg="#7f8c8d", bg="#f0f0f0")
        self._sync_label.pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(toolbar, text="Search:", bg="#f0f0f0").pack(side=tk.RIGHT, padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_search())
        tk.Entry(toolbar, textvariable=self._search_var, width=18).pack(side=tk.RIGHT, padx=(0, 4))
        tk.Button(toolbar, text="✕", command=lambda: self._search_var.set(""), width=2, relief=tk.FLAT).pack(
            side=tk.RIGHT
        )

        outer = tk.Frame(self._parent)
        outer.pack(fill=tk.BOTH, expand=True)

        self._hscroll = ttk.Scrollbar(outer, orient=tk.HORIZONTAL)
        self._hscroll.pack(side=tk.BOTTOM, fill=tk.X)

        self._canvas = tk.Canvas(outer, xscrollcommand=self._hscroll.set, highlightthickness=0, bg="#e8e8e8")
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._hscroll.config(command=self._hscroll_cmd)

        self._cards_frame = tk.Frame(self._canvas, bg="#e8e8e8")
        self._canvas_win = self._canvas.create_window((0, 0), window=self._cards_frame, anchor="nw")
        self._cards_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel)

    # ------------------------------------------------------------------
    # Load & render
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        all_prices = config.load_all_prices()
        name_count: Counter[str] = Counter()
        for prices in all_prices.values():
            for name in prices:
                name_count[name.lower()] += 1
        self._shared_items = {n for n, c in name_count.items() if c > 1}

        for chest_type in self._chest_types:
            prices = all_prices.get(chest_type, {})
            self._vars[chest_type] = {name: tk.StringVar(value=fmt_price(price)) for name, price in prices.items()}
        self._render_cards()

    def _render_cards(self, filter_text: str = "") -> None:
        for w in self._cards_frame.winfo_children():
            w.destroy()
        self._widgets.clear()

        for col, chest_type in enumerate(self._chest_types):
            self._build_card(chest_type, col, filter_text)

        self._cards_frame.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _sorted_items(
        self,
        chest_type: str,
        filter_text: str,
    ) -> tuple[list[tuple[str, tk.StringVar]], list[tuple[str, tk.StringVar]], list[tuple[str, tk.StringVar]]]:
        """
        Return three groups: (pinned, chest_specific, shared_items).
        Within each group, sorted by price descending (0-price at bottom).
        """
        items = self._vars.get(chest_type, {})
        fl = filter_text.strip().lower()

        def price_val(sv: tk.StringVar) -> float:
            try:
                return parse_price(sv.get())
            except ValueError:
                return 0.0

        pinned: list[tuple[str, tk.StringVar]] = []
        specific: list[tuple[str, tk.StringVar]] = []
        shared: list[tuple[str, tk.StringVar]] = []

        for name, sv in items.items():
            if fl and fl not in name.lower():
                continue
            nl = name.lower()
            if nl in _PINNED_LOWER:
                pinned.append((name, sv))
            elif nl in self._shared_items:
                shared.append((name, sv))
            else:
                specific.append((name, sv))

        def by_price(kv: tuple[str, tk.StringVar]) -> float:
            return -price_val(kv[1])

        # Keep pinned in declared order
        pinned.sort(key=lambda kv: _PINNED_LOWER.index(kv[0].lower()))
        specific.sort(key=by_price)
        shared.sort(key=by_price)

        return pinned, specific, shared

    def _build_card(self, chest_type: str, col: int, filter_text: str) -> None:
        card = tk.Frame(self._cards_frame, bg="white", highlightbackground=_BORDER, highlightthickness=1)
        card.grid(row=0, column=col, padx=10, pady=10, sticky="n")
        self._widgets[chest_type] = {}

        hdr_bg, hdr_fg = CHEST_COLOURS.get(chest_type, _DEFAULT_HEADER)
        short = _SHORT_NAMES.get(
            chest_type,
            chest_type.replace("'s Chest", "").replace(" Chest", "").strip(),
        )
        tk.Label(
            card,
            text=short,
            font=("Arial", 12, "bold"),
            fg=hdr_fg,
            bg=hdr_bg,
            pady=8,
            width=_CARD_W // 10,
        ).pack(fill=tk.X)

        # Column headings
        col_head = tk.Frame(card, bg="#f2f2f2")
        col_head.pack(fill=tk.X)
        tk.Label(
            col_head,
            text="Item",
            font=("Arial", 8, "bold"),
            bg="#f2f2f2",
            fg="#555",
            anchor="w",
            padx=10,
            width=20,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            col_head,
            text="Drop%",
            font=("Arial", 8, "bold"),
            bg="#f2f2f2",
            fg="#555",
            anchor="e",
            width=6,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            col_head,
            text="Price",
            font=("Arial", 8, "bold"),
            bg="#f2f2f2",
            fg="#555",
            anchor="e",
            padx=8,
            width=13,
        ).grid(row=0, column=2, sticky="e")
        ttk.Separator(card, orient="horizontal").pack(fill=tk.X)

        # Scrollable row area
        row_outer = tk.Frame(card, bg="white", height=_CARD_ROWS_H)
        row_outer.pack(fill=tk.X)
        row_outer.pack_propagate(False)

        vbar = ttk.Scrollbar(row_outer, orient=tk.VERTICAL)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        row_canvas = tk.Canvas(row_outer, bg="white", highlightthickness=0, yscrollcommand=vbar.set, width=_CARD_W)
        row_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.config(command=row_canvas.yview)

        inner = tk.Frame(row_canvas, bg="white")
        inner_win = row_canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>", lambda e, rc=row_canvas: rc.configure(scrollregion=rc.bbox("all")))
        row_canvas.bind("<Configure>", lambda e, rc=row_canvas, iw=inner_win: rc.itemconfig(iw, width=e.width))

        def _on_wheel(e: tk.Event, rc: tk.Canvas = row_canvas) -> None:  # type: ignore[type-arg]
            rc.yview_scroll(-1 * (e.delta // 120), "units")

        row_canvas.bind("<MouseWheel>", _on_wheel)
        inner.bind("<MouseWheel>", _on_wheel)

        pinned, specific, shared = self._sorted_items(chest_type, filter_text)
        drop_rates = self._drop_rates.get(chest_type, {})

        all_groups: list[tuple[list[tuple[str, tk.StringVar]], str | None]] = [
            (pinned, None),
            (specific, None),
            (shared, "── Shared items ──" if shared else None),
        ]

        row_idx = 0
        for group, separator_label in all_groups:
            if not group:
                continue
            if separator_label:
                sep_frame = tk.Frame(inner, bg="#f0f0f0")
                sep_frame.pack(fill=tk.X)
                tk.Label(
                    sep_frame, text=separator_label, font=("Arial", 7, "italic"), fg="#999", bg="#f0f0f0", pady=2
                ).pack()
                sep_frame.bind("<MouseWheel>", _on_wheel)

            for name, sv in group:
                bg = _ROW_BG if row_idx % 2 == 0 else _ROW_ALT_BG
                chance = drop_rates.get(name)
                lbl, chance_lbl, ent = self._build_row(inner, name, sv, chest_type, bg, chance, _on_wheel)
                self._widgets[chest_type][name] = (lbl, chance_lbl, ent)
                row_idx += 1

        if not (pinned or specific or shared):
            tk.Label(
                inner,
                text="No items" if not filter_text else "No matches",
                fg="gray",
                font=("Arial", 9),
                bg="white",
                pady=12,
            ).pack()

        ttk.Separator(card, orient="horizontal").pack(fill=tk.X, pady=(4, 0))
        tk.Button(
            card,
            text="+ Add item to this chest",
            font=("Arial", 8),
            fg="#3498db",
            bg="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=lambda ct=chest_type: self._add_item_dialog(ct),
        ).pack(pady=(2, 6))

    def _build_row(
        self,
        parent: tk.Frame,
        item_name: str,
        str_var: tk.StringVar,
        chest_type: str,
        bg: str,
        drop_chance: float | None,
        wheel_cb: object,
    ) -> tuple[tk.Label, tk.Label, tk.Entry]:
        is_shared = item_name.lower() in self._shared_items
        is_pinned = item_name.lower() in _PINNED_LOWER
        try:
            is_zero = parse_price(str_var.get()) == 0.0
        except ValueError:
            is_zero = False

        fg = _FG_ZERO if is_zero else _FG_NORMAL
        stripe = "#f39c12" if is_pinned else "#3498db" if is_shared else bg

        row = tk.Frame(parent, bg=bg)
        row.pack(fill=tk.X)
        tk.Frame(row, bg=stripe, width=4).pack(side=tk.LEFT, fill=tk.Y)

        lbl = tk.Label(row, text=item_name, bg=bg, fg=fg, font=("Arial", 9), anchor="w", width=20, padx=6)
        lbl.pack(side=tk.LEFT)

        # Drop chance label
        if drop_chance is None:
            chance_text = ""
        elif drop_chance == 0.0:
            chance_text = "unknown"
        else:
            chance_text = f"{drop_chance:.1f}%"
        chance_lbl = tk.Label(row, text=chance_text, bg=bg, fg=_FG_CHANCE, font=("Arial", 8), width=6, anchor="e")
        chance_lbl.pack(side=tk.LEFT)

        ent = tk.Entry(
            row, textvariable=str_var, width=13, font=("Arial", 9), fg=fg, relief=tk.FLAT, bg=bg, justify="right"
        )
        ent.pack(side=tk.RIGHT, padx=(0, 6), pady=1)

        ent.bind("<FocusOut>", lambda e, n=item_name, ct=chest_type, v=str_var: self._commit(n, ct, v))
        ent.bind("<Return>", lambda e, n=item_name, ct=chest_type, v=str_var: self._commit(n, ct, v))

        for w in (row, lbl, chance_lbl):
            w.bind("<MouseWheel>", wheel_cb)  # type: ignore[arg-type]

        return lbl, chance_lbl, ent

    # ------------------------------------------------------------------
    # Commit — in-place colour update, no redraw
    # ------------------------------------------------------------------

    def _commit(self, item_name: str, source_chest: str, str_var: tk.StringVar) -> None:
        raw = str_var.get()
        try:
            price = parse_price(raw)
        except ValueError:
            return

        str_var.set(fmt_price(price))
        fg = _FG_ZERO if price == 0.0 else _FG_NORMAL
        self._update_row_colour(source_chest, item_name, fg)

        name_lower = item_name.lower()
        synced_to: list[str] = []
        for chest_type, chest_vars in self._vars.items():
            if chest_type == source_chest:
                continue
            for existing_name, existing_var in chest_vars.items():
                if existing_name.lower() == name_lower:
                    existing_var.set(fmt_price(price))
                    self._update_row_colour(chest_type, existing_name, fg)
                    synced_to.append(
                        _SHORT_NAMES.get(
                            chest_type,
                            chest_type.replace("'s Chest", "").replace(" Chest", "").strip(),
                        )
                    )

        if synced_to:
            self._sync_label.config(text=f"↔ '{item_name}' synced to: {', '.join(synced_to)}")
            self._parent.after(4000, lambda: self._sync_label.config(text=""))

    def _update_row_colour(self, chest_type: str, item_name: str, fg: str) -> None:
        triple = self._widgets.get(chest_type, {}).get(item_name)
        if triple is None:
            return
        lbl, chance_lbl, ent = triple
        try:
            lbl.config(fg=fg)
            ent.config(fg=fg)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Drop rates
    # ------------------------------------------------------------------

    def _refresh_drop_rates(self) -> None:
        self._sync_label.config(text="Fetching drop rates…")
        import threading

        threading.Thread(target=self._fetch_drop_rates_worker, daemon=True).start()

    def _fetch_drop_rates_worker(self) -> None:
        all_rates: dict[str, dict[str, float]] = {}
        for chest_type in self._chest_types:
            all_rates[chest_type] = db_handler.fetch_drop_rates(chest_type)
        self._parent.after(0, lambda: self.apply_drop_rates(all_rates))

    def apply_drop_rates(self, all_rates: dict[str, dict[str, float]]) -> None:
        """
        Public — called by app.py on startup and by the Refresh button.
        Updates chance labels in-place without any redraw.
        """
        for chest_type, rates in all_rates.items():
            self._drop_rates[chest_type] = rates
            for item_name, widgets in self._widgets.get(chest_type, {}).items():
                _, chance_lbl, _ = widgets
                chance = rates.get(item_name)
                if chance is None:
                    text = ""
                elif chance == 0.0:
                    text = "unknown"
                else:
                    text = f"{chance:.1f}%"
                try:
                    chance_lbl.config(text=text)
                except tk.TclError:
                    pass
        self._sync_label.config(text="✓ Drop rates updated")
        self._parent.after(3000, lambda: self._sync_label.config(text=""))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self) -> None:
        all_prices: dict[str, dict[str, float]] = {}
        errors: list[str] = []
        for chest_type, chest_vars in self._vars.items():
            prices: dict[str, float] = {}
            for item_name, sv in chest_vars.items():
                try:
                    prices[item_name] = parse_price(sv.get())
                except ValueError:
                    errors.append(f"{chest_type} → {item_name}: '{sv.get()}'")
            all_prices[chest_type] = prices

        if errors:
            messagebox.showerror("Invalid Prices", "Fix these:\n" + "\n".join(errors))
            return

        config.save_all_prices(all_prices)
        self._on_prices_changed(all_prices)
        self._sync_label.config(text="✓ All prices saved")
        self._parent.after(3000, lambda: self._sync_label.config(text=""))

    # ------------------------------------------------------------------
    # Add item
    # ------------------------------------------------------------------

    def _add_item_dialog(self, target_chest: str | None = None) -> None:
        dialog = tk.Toplevel(self._parent)
        dialog.title("Add Item")
        dialog.geometry("360x190")
        dialog.resizable(False, False)
        dialog.grab_set()

        tk.Label(dialog, text="Item name:", anchor="w").grid(row=0, column=0, padx=12, pady=(16, 4), sticky="w")
        name_var = tk.StringVar()
        name_entry = tk.Entry(dialog, textvariable=name_var, width=26)
        name_entry.grid(row=0, column=1, padx=12, pady=(16, 4))
        name_entry.focus_set()

        tk.Label(dialog, text="Price:", anchor="w").grid(row=1, column=0, padx=12, pady=4, sticky="w")
        price_var = tk.StringVar(value="0")
        tk.Entry(dialog, textvariable=price_var, width=26).grid(row=1, column=1, padx=12, pady=4)

        tk.Label(dialog, text="Add to:", anchor="w").grid(row=2, column=0, padx=12, pady=4, sticky="w")
        chest_var = tk.StringVar(value=target_chest or "All chests")
        ttk.Combobox(
            dialog,
            textvariable=chest_var,
            values=["All chests"] + self._chest_types,
            state="readonly",
            width=24,
        ).grid(row=2, column=1, padx=12, pady=4)

        def confirm() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Missing Name", "Item name cannot be empty.", parent=dialog)
                return
            try:
                price = parse_price(price_var.get())
            except ValueError:
                messagebox.showwarning("Invalid Price", "Use a number or k/kk/kkk.", parent=dialog)
                return
            chosen = chest_var.get()
            targets = self._chest_types if chosen == "All chests" else [chosen]
            for ct in targets:
                if ct not in self._vars:
                    self._vars[ct] = {}
                if not any(k.lower() == name.lower() for k in self._vars[ct]):
                    self._vars[ct][name] = tk.StringVar(value=fmt_price(price))
            name_count: Counter[str] = Counter()
            for cv in self._vars.values():
                for n in cv:
                    name_count[n.lower()] += 1
            self._shared_items = {n for n, c in name_count.items() if c > 1}
            dialog.destroy()
            self._render_cards(self._search_var.get())

        tk.Button(
            dialog,
            text="Add",
            command=confirm,
            bg="#3498db",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
        ).grid(row=3, column=0, columnspan=2, pady=12)
        dialog.bind("<Return>", lambda _: confirm())

    # ------------------------------------------------------------------
    # Reload / search
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        self._vars.clear()
        self._load_all()

    def refresh_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        self._vars.clear()
        self._load_all()

    def _apply_search(self) -> None:
        self._render_cards(self._search_var.get())

    # ------------------------------------------------------------------
    # Scroll
    # ------------------------------------------------------------------

    def _hscroll_cmd(self, *args: object) -> None:
        """Proxy for hscroll → canvas.xview that also schedules a redraw."""
        self._canvas.xview(*args)
        self._schedule_canvas_refresh()

    def _schedule_canvas_refresh(self) -> None:
        """Throttle redraws to avoid flicker during fast scrolling."""
        if self._scroll_refresh_id is not None:
            self._parent.after_cancel(self._scroll_refresh_id)
        self._scroll_refresh_id = self._parent.after(16, self._force_redraw)

    def _force_redraw(self) -> None:
        self._scroll_refresh_id = None
        try:
            self._canvas.update_idletasks()
            # Force each card canvas to redraw too
            for child in self._cards_frame.winfo_children():
                child.update_idletasks()
        except tk.TclError:
            pass

    def _on_frame_configure(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        self._canvas.itemconfig(self._canvas_win, height=event.height)

    def _on_shift_mousewheel(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        self._canvas.xview_scroll(-1 * (event.delta // 120), "units")
        self._schedule_canvas_refresh()
