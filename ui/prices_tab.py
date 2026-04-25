"""
ui/prices_tab.py
----------------
Prices tab: horizontally scrollable card panels, one per chest type.

- Header colour/name loaded dynamically from constants (CHEST_COLORS, CHEST_DISPLAY_NAMES)
- Pinned items are per-chest, stored in prices_config.txt, right-click to pin/unpin
- Drop% and avg-drop-qty shown per item
- Items re-sorted after drop rate refresh
- Per-chest refresh button (only refreshes that chest's drop rates)
- No full redraw on price edit — colour updated in-place
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable
from collections import Counter

import prices_config
import db_handler
from chest_definitions import DEFAULT_ITEMS

_DEFAULT_HEADER = ("#555555", "white")

_ROW_BG = "white"
_ROW_ALT_BG = "#f7f7f7"
_BORDER = "#dcdcdc"
_CARD_W = 420
_CARD_ROWS_H = 480
_FG_ZERO = "#b8b8b8"
_FG_NORMAL = "#1a1a1a"
_FG_CHANCE = "#7f8c8d"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _text_colour_for_bg(hex_bg: str) -> str:
    try:
        r, g, b = int(hex_bg[1:3], 16), int(hex_bg[3:5], 16), int(hex_bg[5:7], 16)
        return "white" if (0.299 * r + 0.587 * g + 0.114 * b) < 140 else "#1a252f"
    except Exception:
        return "white"


def _chest_display(chest_type: str) -> tuple[str, str, str]:
    """Return (bg_color, fg_color, short_name) for *chest_type*."""
    from constants import CHEST_COLORS, CHEST_DISPLAY_NAMES

    bg = CHEST_COLORS.get(chest_type, "#555555")
    fg = _text_colour_for_bg(bg)
    short = CHEST_DISPLAY_NAMES.get(
        chest_type,
        chest_type.replace("'s Chest", "").replace(" Chest", "").strip(),
    )
    return bg, fg, short


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


def _safe_parse(raw: str) -> float:
    try:
        return parse_price(raw)
    except ValueError:
        return 0.0


def _fmt_k(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


# ── Widget triple type ────────────────────────────────────────────────────────
# (name_label, drop_label, entry)
_WidgetTriple = tuple[tk.Label, tk.Label, tk.Entry]


def _build_chest_vars(
    chest_type: str,
) -> dict[str, tk.StringVar]:
    """
    Build StringVar dict for a chest type.
    Merges the static default item list with any user-set prices.
    Items with no user price default to "0".
    """
    saved_prices = prices_config.load_prices(chest_type)
    default_items = DEFAULT_ITEMS.get(chest_type, [])

    # Start with all default items at 0
    result: dict[str, tk.StringVar] = {}
    for item in default_items:
        result[item] = tk.StringVar(value="0")

    # Overlay any user-saved prices (match case-insensitively)
    saved_lower = {k.lower(): (k, v) for k, v in saved_prices.items()}
    for item in list(result.keys()):
        match = saved_lower.get(item.lower())
        if match:
            result[item].set(fmt_price(match[1]))

    # Also add any user items that aren't in the default list
    existing_lower = {k.lower() for k in result}
    for item_name, price in saved_prices.items():
        if item_name.lower() not in existing_lower:
            result[item_name] = tk.StringVar(value=fmt_price(price))

    return result


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

        # Per-chest pinned items — loaded once from disk, kept in memory
        self._pinned: dict[str, list[str]] = {}

        # Live widget refs for in-place updates
        self._widgets: dict[str, dict[str, _WidgetTriple]] = {}
        self._avg_labels: dict[str, tk.Label] = {}
        self._all_entries: dict[str, list[tk.Entry]] = {}

        # Drop rate + avg qty data
        self._drop_rates: dict[str, dict[str, float]] = {}
        self._avg_qty: dict[str, dict[str, float]] = {}
        self._chest_stats: dict[str, db_handler.Stats] = {}

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
            text="Save All Prices",
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
            text="Add Item",
            font=("Arial", 10),
            bg="#3498db",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=lambda: self._add_item_dialog(),
        ).pack(side=tk.LEFT, padx=(0, 6))

        self._sync_label = tk.Label(toolbar, text="", font=("Arial", 8), fg="#7f8c8d", bg="#f0f0f0")
        self._sync_label.pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(toolbar, text="Search:", bg="#f0f0f0").pack(side=tk.RIGHT, padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_search())
        tk.Entry(toolbar, textvariable=self._search_var, width=18).pack(side=tk.RIGHT, padx=(0, 4))
        tk.Button(toolbar, text="X", command=lambda: self._search_var.set(""), width=2, relief=tk.FLAT).pack(
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
        # Load pinned items from disk for every chest type
        for chest_type in self._chest_types:
            self._pinned[chest_type] = prices_config.load_pinned_items(chest_type)

        # Build vars merging defaults + saved prices
        all_saved = prices_config.load_all_prices()
        name_count: Counter[str] = Counter()

        for chest_type in self._chest_types:
            self._vars[chest_type] = _build_chest_vars(chest_type)
            for name in self._vars[chest_type]:
                name_count[name.lower()] += 1

        self._shared_items = {n for n, c in name_count.items() if c > 1}
        self._render_cards()

    def _render_cards(self, filter_text: str = "") -> None:
        for w in self._cards_frame.winfo_children():
            w.destroy()
        self._widgets.clear()
        self._avg_labels = {}
        self._all_entries.clear()

        for col, chest_type in enumerate(self._chest_types):
            self._build_card(chest_type, col, filter_text)

        self._cards_frame.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _sorted_groups(
        self,
        chest_type: str,
        filter_text: str,
    ) -> tuple[
        list[tuple[str, tk.StringVar]],
        list[tuple[str, tk.StringVar]],
        list[tuple[str, tk.StringVar]],
    ]:
        items = self._vars.get(chest_type, {})
        fl = filter_text.strip().lower()
        pinned_lower = [p.lower() for p in self._pinned.get(chest_type, [])]

        def price_val(sv: tk.StringVar) -> float:
            try:
                return parse_price(sv.get())
            except ValueError:
                return 0.0

        def by_price(kv: tuple[str, tk.StringVar]) -> float:
            return -price_val(kv[1])

        pinned: list[tuple[str, tk.StringVar]] = []
        specific: list[tuple[str, tk.StringVar]] = []
        shared: list[tuple[str, tk.StringVar]] = []

        for name, sv in items.items():
            if fl and fl not in name.lower():
                continue
            nl = name.lower()
            if nl in pinned_lower:
                pinned.append((name, sv))
            elif nl in self._shared_items:
                shared.append((name, sv))
            else:
                specific.append((name, sv))

        pinned.sort(key=lambda kv: pinned_lower.index(kv[0].lower()) if kv[0].lower() in pinned_lower else 999)
        specific.sort(key=by_price)
        shared.sort(key=by_price)
        return pinned, specific, shared

    def _build_card(self, chest_type: str, col: int, filter_text: str) -> None:
        card = tk.Frame(self._cards_frame, bg="white", highlightbackground=_BORDER, highlightthickness=1)
        card.grid(row=0, column=col, padx=10, pady=10, sticky="n")
        self._widgets[chest_type] = {}

        hdr_bg, hdr_fg, short = _chest_display(chest_type)

        # Header row: title + refresh button
        hdr_frame = tk.Frame(card, bg=hdr_bg)
        hdr_frame.pack(fill=tk.X)

        tk.Label(
            hdr_frame,
            text=short,
            font=("Arial", 12, "bold"),
            fg=hdr_fg,
            bg=hdr_bg,
            width=_CARD_W // 10,
            pady=8,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Per-chest refresh button
        refresh_btn = tk.Button(
            hdr_frame,
            text="↻",
            font=("Arial", 11, "bold"),
            fg=hdr_fg,
            bg=hdr_bg,
            relief=tk.FLAT,
            cursor="hand2",
            padx=6,
            command=lambda ct=chest_type: self._refresh_single_chest(ct),
        )
        refresh_btn.pack(side=tk.RIGHT, padx=(0, 6))

        avg_label = tk.Label(card, text="avg: —", font=("Arial", 8), fg=hdr_fg, bg=hdr_bg)
        avg_label.pack(fill=tk.X, pady=(0, 6))
        self._avg_labels[chest_type] = avg_label

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
            text="Avg",
            font=("Arial", 8, "bold"),
            bg="#f2f2f2",
            fg="#555",
            anchor="e",
            width=7,
        ).grid(row=0, column=2, sticky="e")
        tk.Label(
            col_head,
            text="Price",
            font=("Arial", 8, "bold"),
            bg="#f2f2f2",
            fg="#555",
            anchor="e",
            padx=8,
            width=12,
        ).grid(row=0, column=3, sticky="e")
        ttk.Separator(card, orient="horizontal").pack(fill=tk.X)

        # Scrollable rows
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

        pinned, specific, shared = self._sorted_groups(chest_type, filter_text)
        drop_rates = self._drop_rates.get(chest_type, {})
        avg_qty = self._avg_qty.get(chest_type, {})

        groups: list[tuple[list[tuple[str, tk.StringVar]], str | None]] = [
            (pinned, None),
            (specific, None),
            (shared, "── Shared items ──" if shared else None),
        ]

        row_idx = 0
        for group, sep_label in groups:
            if not group:
                continue
            if sep_label:
                sep_frame = tk.Frame(inner, bg="#f0f0f0")
                sep_frame.pack(fill=tk.X)
                tk.Label(
                    sep_frame, text=sep_label, font=("Arial", 7, "italic"), fg="#999", bg="#f0f0f0", pady=2
                ).pack()
                sep_frame.bind("<MouseWheel>", _on_wheel)

            for name, sv in group:
                bg = _ROW_BG if row_idx % 2 == 0 else _ROW_ALT_BG
                chance = drop_rates.get(name)
                avg = avg_qty.get(name)
                triple = self._build_row(inner, name, sv, chest_type, bg, chance, avg, _on_wheel)
                self._widgets[chest_type][name] = triple
                triple[0].bind("<MouseWheel>", _on_wheel)
                triple[1].bind("<MouseWheel>", _on_wheel)
                triple[2].bind("<MouseWheel>", _on_wheel)
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
        avg_qty: float | None,
        wheel_cb: object,
    ) -> _WidgetTriple:
        pinned_lower = [p.lower() for p in self._pinned.get(chest_type, [])]
        is_pinned = item_name.lower() in pinned_lower
        is_shared = item_name.lower() in self._shared_items
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

        # Drop chance
        if drop_chance is None:
            chance_text = ""
        elif drop_chance == 0.0:
            chance_text = "unknown"
        else:
            chance_text = f"{drop_chance:.1f}%"

        # Avg qty
        avg_text = f"{avg_qty:.1f}" if avg_qty is not None and avg_qty > 0 else ""

        drop_lbl = tk.Label(row, text=chance_text, bg=bg, fg=_FG_CHANCE, font=("Arial", 8), width=7, anchor="e")
        drop_lbl.pack(side=tk.LEFT)

        avg_lbl_item = tk.Label(row, text=avg_text, bg=bg, fg=_FG_CHANCE, font=("Arial", 8), width=7, anchor="e")
        avg_lbl_item.pack(side=tk.LEFT)

        ent = tk.Entry(
            row, textvariable=str_var, width=12, font=("Arial", 9), fg=fg, relief=tk.FLAT, bg=bg, justify="right"
        )
        ent.pack(side=tk.RIGHT, padx=(0, 6), pady=1)

        ent.bind("<FocusOut>", lambda e, n=item_name, ct=chest_type, v=str_var: self._commit(n, ct, v))
        ent.bind("<Return>", lambda e, n=item_name, ct=chest_type, v=str_var: self._commit(n, ct, v))

        # Right-click to pin/unpin
        def _pin_menu(e: tk.Event, n: str = item_name, ct: str = chest_type) -> None:  # type: ignore[type-arg]
            menu = tk.Menu(self._parent, tearoff=0)
            pl = [p.lower() for p in self._pinned.get(ct, [])]
            if n.lower() in pl:
                menu.add_command(label=f"Unpin '{n}'", command=lambda: self._toggle_pin(ct, n))
            else:
                menu.add_command(label=f"Pin '{n}' to top", command=lambda: self._toggle_pin(ct, n))
            menu.tk_popup(e.x_root, e.y_root)

        lbl.bind("<Button-3>", _pin_menu)
        ent.bind("<Button-3>", _pin_menu)

        # Shared-item cross-card highlight
        nl = item_name.lower()
        if nl not in self._all_entries:
            self._all_entries[nl] = []
        self._all_entries[nl].append(ent)
        if nl in self._shared_items:
            ent.bind("<FocusIn>", lambda e, n=nl: self._highlight_shared(n, True))
            ent.bind(
                "<FocusOut>",
                lambda e, n=nl, ct=chest_type, v=str_var, nm=item_name: (
                    self._highlight_shared(n, False),
                    self._commit(nm, ct, v),
                ),
                add="+",
            )

        return lbl, drop_lbl, ent

    # ------------------------------------------------------------------
    # Commit — in-place colour update, no full redraw
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
                    _, _, short = _chest_display(chest_type)
                    synced_to.append(short)
        if synced_to:
            self._sync_label.config(text=f"'{item_name}' synced to: {', '.join(synced_to)}")
            self._parent.after(4000, lambda: self._sync_label.config(text=""))

    def _update_row_colour(self, chest_type: str, item_name: str, fg: str) -> None:
        triple = self._widgets.get(chest_type, {}).get(item_name)
        if triple is None:
            return
        lbl, drop_lbl, ent = triple
        try:
            lbl.config(fg=fg)
            ent.config(fg=fg)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Pin / unpin — persists immediately to disk
    # ------------------------------------------------------------------

    def _toggle_pin(self, chest_type: str, item_name: str) -> None:
        pinned = list(self._pinned.get(chest_type, []))
        lower = item_name.lower()
        if lower in [p.lower() for p in pinned]:
            pinned = [p for p in pinned if p.lower() != lower]
        else:
            pinned = pinned + [item_name]
        self._pinned[chest_type] = pinned
        # Persist immediately so it survives app restart
        prices_config.save_pinned_items(chest_type, pinned)
        self._render_cards(self._search_var.get())

    # ------------------------------------------------------------------
    # Drop rates — per-chest refresh
    # ------------------------------------------------------------------

    def _refresh_single_chest(self, chest_type: str) -> None:
        """Refresh drop rates/stats for a single chest only."""
        _, _, short = _chest_display(chest_type)
        self._sync_label.config(text=f"Refreshing {short}...")
        import threading

        threading.Thread(
            target=self._fetch_single_chest_worker,
            args=(chest_type,),
            daemon=True,
        ).start()

    def _fetch_single_chest_worker(self, chest_type: str) -> None:
        rates = db_handler.fetch_drop_rates(chest_type)
        avgs = db_handler.fetch_avg_quantities(chest_type)
        saved = prices_config.load_prices(chest_type)
        prices_lower = {k.lower(): v for k, v in saved.items()}
        stats = db_handler.calculate_statistics(chest_type, prices_lower)

        def _apply() -> None:
            self._drop_rates[chest_type] = rates
            self._avg_qty[chest_type] = avgs
            self._chest_stats[chest_type] = stats
            # Rebuild only this chest's card widgets
            self._render_cards(self._search_var.get())
            self._update_avg_label(chest_type, rates)
            _, _, short = _chest_display(chest_type)
            self._sync_label.config(text=f"{short} refreshed")
            self._parent.after(3000, lambda: self._sync_label.config(text=""))

        self._parent.after(0, _apply)

    def apply_drop_rates(
        self,
        all_rates: dict[str, dict[str, float]],
        all_stats: dict[str, db_handler.Stats] | None = None,
        all_avgs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """Called from app startup to bulk-load all chest drop data."""
        if all_stats is not None:
            self._chest_stats = all_stats
        if all_avgs is not None:
            self._avg_qty = all_avgs

        for chest_type, rates in all_rates.items():
            self._drop_rates[chest_type] = rates

        self._render_cards(self._search_var.get())

        for chest_type in self._chest_types:
            rates = self._drop_rates.get(chest_type, {})
            self._update_avg_label(chest_type, rates)

        self._sync_label.config(text="Drop rates loaded")
        self._parent.after(3000, lambda: self._sync_label.config(text=""))

    def _update_avg_label(self, chest_type: str, rates: dict[str, float]) -> None:
        avg_lbl = self._avg_labels.get(chest_type)
        if avg_lbl is None:
            return
        stats = self._chest_stats.get(chest_type)
        hdr_bg, hdr_fg, _ = _chest_display(chest_type)
        if stats is not None and stats.avg_revenue_per_chest > 0:
            text = f"avg {_fmt_k(stats.avg_revenue_per_chest)}  ·  {stats.total_chests} chests"
        else:
            saved = prices_config.load_prices(chest_type)
            prices_lower = {k.lower(): v for k, v in saved.items()}
            expected = sum((rates.get(name, 0.0) / 100.0) * prices_lower.get(name.lower(), 0.0) for name in rates)
            text = f"est. avg {_fmt_k(expected)}" if expected > 0 else "avg: —"
        try:
            avg_lbl.config(text=text, fg=hdr_fg, bg=hdr_bg)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Shared-item highlight
    # ------------------------------------------------------------------

    def _highlight_shared(self, item_name_lower: str, active: bool) -> None:
        colour = "#fff3cd" if active else None
        for ent in self._all_entries.get(item_name_lower, []):
            try:
                ent.config(bg=colour if colour else "white")
            except tk.TclError:
                pass

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
                    errors.append(f"{chest_type} -> {item_name}: '{sv.get()}'")
            all_prices[chest_type] = prices
        if errors:
            messagebox.showerror("Invalid Prices", "Fix these:\n" + "\n".join(errors))
            return
        prices_config.save_all_prices(all_prices)
        self._on_prices_changed(all_prices)
        self._sync_label.config(text="All prices saved")
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
    # Search / refresh
    # ------------------------------------------------------------------

    def _apply_search(self) -> None:
        self._render_cards(self._search_var.get())

    def refresh_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        self._vars.clear()
        self._pinned.clear()
        self._load_all()

    # ------------------------------------------------------------------
    # Scroll helpers
    # ------------------------------------------------------------------

    def _hscroll_cmd(self, *args: object) -> None:
        self._canvas.xview(*args)
        self._schedule_canvas_refresh()

    def _schedule_canvas_refresh(self) -> None:
        if self._scroll_refresh_id is not None:
            self._parent.after_cancel(self._scroll_refresh_id)
        self._scroll_refresh_id = self._parent.after(16, self._force_redraw)

    def _force_redraw(self) -> None:
        self._scroll_refresh_id = None
        try:
            self._canvas.update_idletasks()
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
