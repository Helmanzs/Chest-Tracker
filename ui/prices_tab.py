"""
ui/prices_tab.py  (Dear PyGui port)
--------------------------------------
Prices tab: horizontally scrollable card panels, one per chest type.

Layout
------
  card_area : child_window(w=-1, h=-1, horizontal_scrollbar=True)
    group(horizontal=True)
      card : child_window(w=CARD_W, h=CARD_H)   <- FIXED height, no scroll
        hdr_win  : child_window coloured band
        avg text
        separator + heading row + separator
        rows_win : child_window(h=-1)            <- only scrolling element
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Callable

import dearpygui.dearpygui as dpg

import prices_config
import db_handler
from chest_definitions import DEFAULT_ITEMS
from constants import CHEST_COLORS, CHEST_DISPLAY_NAMES

# ---------------------------------------------------------------------------
# Layout constants  (all in pixels)
# ---------------------------------------------------------------------------

_W_STRIPE = 8
_W_NAME = 178
_W_DROP = 66
_W_AVG = 54
_W_PRICE = 114

_INNER_W = (_W_STRIPE + 8) + _W_NAME + _W_DROP + _W_AVG + _W_PRICE  # = 428
_CARD_W = _INNER_W + 20  # = 448

_HDR_H = 56
_AVG_H = 22
_HEAD_H = 22
_ROW_H = 22
_ROWS_CNT = 20
_ROWS_H = _ROW_H * _ROWS_CNT
_CARD_H = _HDR_H + _AVG_H + 4 + _HEAD_H + 4 + _ROWS_H + 16
_CARD_GAP = 10

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

_FG_ZERO = (105, 105, 105, 255)
_FG_NORMAL = (220, 220, 220, 255)
_FG_CHANCE = (148, 148, 148, 255)
_ROW_EVEN = (48, 48, 54, 255)
_ROW_ODD = (40, 40, 46, 255)
_COL_HDR_FG = (190, 190, 190, 255)

# Module-level font ID (populated by _ensure_fonts on first use)
_font_large: int = 0


def _ensure_fonts() -> None:
    """Load large header font once into its own registry; safe to call multiple times."""
    global _font_large
    if _font_large:
        return
    import os, sys

    candidates: list[str] = []
    if sys.platform == "win32":
        candidates = [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if path is None:
        print("[prices_tab] no TTF found for large header font")
        return
    try:
        reg = dpg.add_font_registry()
        font_id: int = dpg.add_font(path, 22, parent=reg)  # type: ignore[assignment]
        _font_large = font_id
        print(f"[prices_tab] large font loaded: {path} -> id={_font_large}")
    except Exception as exc:
        print(f"[prices_tab] font load failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_bg: str) -> tuple[int, int, int]:
    try:
        return int(hex_bg[1:3], 16), int(hex_bg[3:5], 16), int(hex_bg[5:7], 16)
    except Exception:
        return (85, 85, 85)


def _chest_display(chest_type: str) -> tuple[tuple[int, int, int], str]:
    bg_hex = CHEST_COLORS.get(chest_type, "#555555")
    rgb = _hex_to_rgb(bg_hex)
    short = CHEST_DISPLAY_NAMES.get(
        chest_type,
        chest_type.replace("'s Chest", "").replace(" Chest", "").strip(),
    )
    return rgb, short


def _text_col_for_bg(rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (255, 255, 255, 255) if lum < 140 else (20, 20, 20, 255)


def parse_price(raw: str) -> float:
    s = raw.strip().replace(" ", "").replace(",", "").lower()
    if not s:
        return 0.0
    mult = 1
    if s.endswith("kkk"):
        mult, s = 1_000_000_000, s[:-3]
    elif s.endswith("kk"):
        mult, s = 1_000_000, s[:-2]
    elif s.endswith("k"):
        mult, s = 1_000, s[:-1]
    return float(s) * mult


def fmt_price(price: float) -> str:
    if price == int(price):
        return f"{int(price):,}".replace(",", " ")
    return f"{price:,.2f}".replace(",", " ")


def _safe_parse(raw: str) -> float:
    try:
        return parse_price(raw)
    except (ValueError, OverflowError):
        return 0.0


def _fmt_k(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


def _build_chest_vars(chest_type: str) -> dict[str, str]:
    saved = prices_config.load_prices(chest_type)
    defaults = DEFAULT_ITEMS.get(chest_type, [])
    saved_lower = {k.lower(): (k, v) for k, v in saved.items()}
    result: dict[str, str] = {}
    for item in defaults:
        match = saved_lower.get(item.lower())
        result[item] = fmt_price(match[1]) if match else "0"
    return result


def _make_hdr_theme(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    tid: int = dpg.add_theme()  # type: ignore[assignment]
    with dpg.theme_component(dpg.mvChildWindow, parent=tid):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (r, g, b, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Border, (r, g, b, 255))
        dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (r, g, b, 255))
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    return tid


def _make_btn_theme(rgb: tuple[int, int, int], text_col: tuple[int, int, int, int]) -> int:
    r = max(0, rgb[0] - 35)
    g = max(0, rgb[1] - 35)
    b = max(0, rgb[2] - 35)
    tid: int = dpg.add_theme()  # type: ignore[assignment]
    with dpg.theme_component(dpg.mvButton, parent=tid):
        dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 200))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (max(0, r - 20), max(0, g - 20), max(0, b - 20), 255))
        dpg.add_theme_color(dpg.mvThemeCol_Text, text_col)
    return tid


# ---------------------------------------------------------------------------
# PricesTab
# ---------------------------------------------------------------------------


class PricesTab:
    def __init__(
        self,
        parent_tag: str | int,
        chest_types: list[str],
        on_prices_changed: Callable[[dict[str, dict[str, float]]], None],
    ) -> None:
        self._parent = parent_tag
        self._chest_types = chest_types
        self._on_prices_changed = on_prices_changed

        self._vars: dict[str, dict[str, str]] = {}
        self._shared_items: set[str] = set()
        self._pinned: dict[str, list[str]] = {}
        self._drop_rates: dict[str, dict[str, float]] = {}
        self._avg_qty: dict[str, dict[str, float]] = {}
        self._chest_stats: dict[str, db_handler.Stats] = {}

        self._search_text = ""
        self._ids: dict[str, int | str] = {}
        self._hdr_themes: dict[str, int] = {}
        self._btn_themes: dict[str, int] = {}
        self._loading_chests: set[str] = set()

        _ensure_fonts()
        self._build()
        self._load_all()

    # ------------------------------------------------------------------
    # Scaffold
    # ------------------------------------------------------------------

    def _build(self) -> None:
        with dpg.group(parent=self._parent):
            with dpg.group(horizontal=True):
                save_btn = dpg.add_button(
                    label="Save All Prices",
                    height=32,
                    width=150,
                    callback=self._save_all,
                )
                with dpg.theme() as _st:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(dpg.mvThemeCol_Button, (46, 204, 113, 220))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 204, 113, 255))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (27, 152, 79, 255))
                dpg.bind_item_theme(save_btn, _st)

                dpg.add_spacer(width=16)
                self._ids["sync_label"] = dpg.add_text("", color=(127, 140, 141, 255))
                dpg.add_spacer(width=20)
                dpg.add_text("Search:")
                self._ids["search_input"] = dpg.add_input_text(
                    label="",
                    width=200,
                    hint="filter items...",
                    callback=self._on_search,
                )
                dpg.add_button(label="X", width=26, callback=self._clear_search)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            self._ids["card_area"] = dpg.add_child_window(
                width=-1,
                height=_CARD_H + 22,
                horizontal_scrollbar=True,
                no_scrollbar=False,
                border=False,
            )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        for ct in self._chest_types:
            self._pinned[ct] = prices_config.load_pinned_items(ct)
        name_count: Counter[str] = Counter()
        for ct in self._chest_types:
            self._vars[ct] = _build_chest_vars(ct)
            for name in self._vars[ct]:
                name_count[name.lower()] += 1
        self._shared_items = {n for n, c in name_count.items() if c > 1}
        self._render_cards()

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _render_cards(self) -> None:
        area = self._ids.get("card_area")
        if not area or not dpg.does_item_exist(area):
            return
        dpg.delete_item(area, children_only=True)
        if not self._chest_types:
            return

        with dpg.group(parent=area, horizontal=True):
            for ct in self._chest_types:
                self._build_card(ct)
                dpg.add_spacer(width=_CARD_GAP)

    # ------------------------------------------------------------------
    # Single card
    # ------------------------------------------------------------------

    def _build_card(self, chest_type: str) -> None:
        rgb, short = _chest_display(chest_type)
        text_col = _text_col_for_bg(rgb)
        hdr_key = f"{rgb[0]}_{rgb[1]}_{rgb[2]}"

        if hdr_key not in self._hdr_themes:
            self._hdr_themes[hdr_key] = _make_hdr_theme(rgb)
        if hdr_key not in self._btn_themes:
            self._btn_themes[hdr_key] = _make_btn_theme(rgb, text_col)

        with dpg.child_window(width=_CARD_W, height=_CARD_H, border=True, no_scrollbar=True):
            # --- Coloured header band ---
            hdr_win = dpg.add_child_window(
                width=_CARD_W - 4,
                height=_HDR_H,
                border=False,
                no_scrollbar=True,
            )
            dpg.bind_item_theme(hdr_win, self._hdr_themes[hdr_key])

            _btn_w = 80
            _txt_col_w = _CARD_W - 4 - _btn_w - 4

            with dpg.group(parent=hdr_win):
                dpg.add_spacer(height=12)
                hdr_tbl = dpg.add_table(
                    header_row=False,
                    borders_innerV=False,
                    policy=dpg.mvTable_SizingFixedFit,
                    pad_outerX=False,
                )
                dpg.add_table_column(
                    parent=hdr_tbl,
                    width_fixed=True,
                    init_width_or_weight=float(_txt_col_w),
                )
                dpg.add_table_column(
                    parent=hdr_tbl,
                    width_fixed=True,
                    init_width_or_weight=float(_btn_w),
                )
                with dpg.table_row(parent=hdr_tbl):
                    with dpg.group(horizontal=True):
                        dpg.add_spacer(width=10)
                        lbl = dpg.add_text(short, color=text_col)
                        if _font_large:
                            dpg.bind_item_font(lbl, _font_large)
                    ref_btn = dpg.add_button(
                        label="Refresh",
                        width=_btn_w,
                        height=28,
                        user_data=chest_type,
                        callback=self._on_refresh_click,
                    )
                    dpg.bind_item_theme(ref_btn, self._btn_themes[hdr_key])

            # --- Avg revenue line (with inline spinner when loading) ---
            stats = self._chest_stats.get(chest_type)
            if stats and stats.avg_revenue_per_chest > 0:
                avg_text = f"avg {_fmt_k(stats.avg_revenue_per_chest)}  |  {stats.total_chests} chests"
            else:
                avg_text = "avg: --"
            with dpg.group(horizontal=True):
                self._ids[f"avg_{chest_type}"] = dpg.add_text(
                    avg_text,
                    color=(160, 160, 160, 255),
                    indent=6,
                )
                if chest_type in self._loading_chests:
                    dpg.add_spacer(width=8)
                    dpg.add_loading_indicator(
                        style=1,
                        radius=3.5,
                        speed=2.5,
                        thickness=2.0,
                        circle_count=6,
                        color=(160, 160, 160, 255),
                        secondary_color=(50, 50, 58, 255),
                    )

            dpg.add_separator()
            self._build_heading_row()
            dpg.add_separator()

            # --- Scrollable item list ---
            with dpg.child_window(width=_CARD_W - 4, height=-1, border=False, no_scrollbar=False):
                pinned, specific, shared = self._sorted_groups(chest_type)
                dr = self._drop_rates.get(chest_type, {})
                aq = self._avg_qty.get(chest_type, {})
                row_idx = 0
                for name, ps in pinned:
                    self._build_row(chest_type, name, ps, dr, aq, row_idx, is_pinned=True)
                    row_idx += 1
                for name, ps in specific:
                    self._build_row(chest_type, name, ps, dr, aq, row_idx)
                    row_idx += 1
                if shared:
                    dpg.add_text("-- Shared items --", color=(100, 100, 100, 255), indent=6)
                for name, ps in shared:
                    self._build_row(chest_type, name, ps, dr, aq, row_idx, is_shared=True)
                    row_idx += 1
                if not (pinned or specific or shared):
                    dpg.add_spacer(height=10)
                    dpg.add_text(
                        "No items" if not self._search_text else "No matches",
                        color=(160, 160, 160, 255),
                        indent=8,
                    )

    # ------------------------------------------------------------------
    # Heading row
    # ------------------------------------------------------------------

    def _build_heading_row(self) -> None:
        tbl = dpg.add_table(
            header_row=False,
            borders_innerV=False,
            policy=dpg.mvTable_SizingFixedFit,
            pad_outerX=False,
        )
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_STRIPE + 8))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_NAME))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_DROP))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_AVG))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_PRICE))
        with dpg.table_row(parent=tbl):
            dpg.add_text("")
            dpg.add_text("Item", color=_COL_HDR_FG)
            dpg.add_text("Drop%", color=_COL_HDR_FG)
            dpg.add_text("Avg", color=_COL_HDR_FG)
            dpg.add_text("Price", color=_COL_HDR_FG)

    # ------------------------------------------------------------------
    # Item row
    # ------------------------------------------------------------------

    def _build_row(
        self,
        chest_type: str,
        item_name: str,
        price_str: str,
        drop_rates: dict[str, float],
        avg_qty: dict[str, float],
        row_idx: int,
        is_pinned: bool = False,
        is_shared: bool = False,
    ) -> None:
        is_zero = _safe_parse(price_str) == 0.0
        fg = _FG_ZERO if is_zero else _FG_NORMAL
        row_bg = _ROW_EVEN if row_idx % 2 == 0 else _ROW_ODD

        if is_pinned:
            stripe_col: tuple[int, int, int, int] = (243, 156, 18, 255)
        elif is_shared:
            stripe_col = (52, 152, 219, 255)
        else:
            stripe_col = row_bg

        chance = drop_rates.get(item_name)
        avg = avg_qty.get(item_name)
        if chance is None:
            chance_text = ""
        elif chance == 0.0:
            chance_text = "?"
        else:
            chance_text = f"{chance:.1f}%"
        avg_text = f"{avg:.1f}" if avg and avg > 0 else ""

        tbl = dpg.add_table(
            header_row=False,
            borders_innerV=False,
            policy=dpg.mvTable_SizingFixedFit,
            pad_outerX=False,
        )
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_STRIPE + 8))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_NAME))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_DROP))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_AVG))
        dpg.add_table_column(parent=tbl, width_fixed=True, init_width_or_weight=float(_W_PRICE))

        with dpg.table_row(parent=tbl):
            dpg.add_color_button(
                default_value=stripe_col,
                width=_W_STRIPE,
                height=14,
                no_tooltip=True,
                enabled=False,
            )
            name_lbl = dpg.add_text(item_name[:26], color=fg)
            with dpg.popup(parent=name_lbl, mousebutton=dpg.mvMouseButton_Right):
                pinned_now = item_name.lower() in [p.lower() for p in self._pinned.get(chest_type, [])]
                if pinned_now:
                    dpg.add_menu_item(
                        label=f"Unpin '{item_name[:20]}'",
                        user_data=(chest_type, item_name),
                        callback=self._on_pin_click,
                    )
                else:
                    dpg.add_menu_item(
                        label=f"Pin '{item_name[:20]}' to top",
                        user_data=(chest_type, item_name),
                        callback=self._on_pin_click,
                    )
            dpg.add_text(chance_text, color=_FG_CHANCE)
            dpg.add_text(avg_text, color=_FG_CHANCE)
            inp_tag = self._tag_for(chest_type, item_name)
            if dpg.does_item_exist(inp_tag):
                dpg.delete_item(inp_tag)
            dpg.add_input_text(
                tag=inp_tag,
                default_value=price_str,
                width=_W_PRICE - 4,
                label="",
                user_data=(chest_type, item_name),
                callback=self._on_price_change,
            )
            with dpg.item_handler_registry() as _reg:
                dpg.add_item_activated_handler(
                    user_data=(chest_type, item_name),
                    callback=self._on_price_focus_in,
                )
                dpg.add_item_deactivated_handler(
                    user_data=(chest_type, item_name, inp_tag),
                    callback=self._on_price_focus_out,
                )
            dpg.bind_item_handler_registry(inp_tag, _reg)
        dpg.highlight_table_row(tbl, 0, row_bg)

    # ------------------------------------------------------------------
    # Pin / unpin
    # ------------------------------------------------------------------

    def _toggle_pin(self, chest_type: str, item_name: str) -> None:
        """Toggle pinned state for item_name in chest_type, then re-render."""
        pinned = list(self._pinned.get(chest_type, []))
        lower = item_name.lower()
        if lower in [p.lower() for p in pinned]:
            pinned = [p for p in pinned if p.lower() != lower]
        else:
            pinned = pinned + [item_name]
        self._pinned[chest_type] = pinned
        prices_config.save_pinned_items(chest_type, pinned)
        self._render_cards()

    def _sorted_groups(
        self,
        chest_type: str,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
        items = self._vars.get(chest_type, {})
        fl = self._search_text.strip().lower()
        pinned_lower = [p.lower() for p in self._pinned.get(chest_type, [])]

        pinned_list: list[tuple[str, str]] = []
        specific: list[tuple[str, str]] = []
        shared: list[tuple[str, str]] = []

        for name, sv in items.items():
            if fl and fl not in name.lower():
                continue
            nl = name.lower()
            if nl in pinned_lower:
                pinned_list.append((name, sv))
            elif nl in self._shared_items:
                shared.append((name, sv))
            else:
                specific.append((name, sv))

        pinned_list.sort(key=lambda kv: pinned_lower.index(kv[0].lower()) if kv[0].lower() in pinned_lower else 999)
        specific.sort(key=lambda kv: -_safe_parse(kv[1]))
        shared.sort(key=lambda kv: -_safe_parse(kv[1]))
        return pinned_list, specific, shared

    def _tag_for(self, chest_type: str, item_name: str) -> str:
        def _s(v: str) -> str:
            return "".join(c if c.isalnum() or c == "_" else "_" for c in v)

        return f"pi_{_s(chest_type)[:18]}_{_s(item_name)[:28]}"

    # ------------------------------------------------------------------
    # DPG callbacks
    # ------------------------------------------------------------------

    def _on_price_change(self, sender: int, app_data: str, user_data: object) -> None:
        """Fires on every keystroke — syncs raw value to sibling fields immediately."""
        if not isinstance(user_data, tuple) or len(user_data) != 2:
            return
        chest_type, item_name = user_data
        raw = dpg.get_value(sender)
        name_lower = item_name.lower()
        for other_ct, other_vars in self._vars.items():
            if other_ct == chest_type:
                continue
            for existing_name in other_vars:
                if existing_name.lower() == name_lower:
                    other_tag = self._tag_for(other_ct, existing_name)
                    if dpg.does_item_exist(other_tag):
                        dpg.set_value(other_tag, raw)

    def _on_price_focus_in(self, sender: int, app_data: object, user_data: object) -> None:
        """Fires when a price field gains focus — highlight all sibling fields."""
        if not isinstance(user_data, tuple) or len(user_data) != 2:
            return
        chest_type, item_name = user_data
        sibling_tags = self._sibling_tags(chest_type, item_name)
        self._set_field_highlight(sibling_tags, active=True)

    def _on_price_focus_out(self, sender: int, app_data: object, user_data: object) -> None:
        """Fires when a price field loses focus — format value and clear highlight."""
        if not isinstance(user_data, tuple) or len(user_data) != 3:
            return
        chest_type, item_name, inp_tag = user_data
        if dpg.does_item_exist(inp_tag):
            self._commit(chest_type, item_name, inp_tag)
        sibling_tags = self._sibling_tags(chest_type, item_name)
        self._set_field_highlight(sibling_tags, active=False)

    def _sibling_tags(self, chest_type: str, item_name: str) -> list[str]:
        """Return tags of the same item in all other chests."""
        name_lower = item_name.lower()
        tags: list[str] = []
        for other_ct, other_vars in self._vars.items():
            if other_ct == chest_type:
                continue
            for existing_name in other_vars:
                if existing_name.lower() == name_lower:
                    t = self._tag_for(other_ct, existing_name)
                    if dpg.does_item_exist(t):
                        tags.append(t)
        return tags

    def _set_field_highlight(self, tags: list[str], active: bool) -> None:
        """Apply or remove an amber highlight theme on the given input fields."""
        colour = (90, 80, 30, 255) if active else (50, 50, 58, 255)
        for t in tags:
            if dpg.does_item_exist(t):
                th: int = dpg.add_theme()  # type: ignore[assignment]
                with dpg.theme_component(dpg.mvInputText, parent=th):
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, colour)
                dpg.bind_item_theme(t, th)

    def _on_pin_click(self, sender: int, app_data: object, user_data: object) -> None:
        if not isinstance(user_data, tuple) or len(user_data) != 2:
            return
        chest_type, item_name = user_data
        self._toggle_pin(chest_type, item_name)

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    def _commit(self, chest_type: str, item_name: str, sender: int | str) -> None:
        raw = dpg.get_value(sender)
        try:
            price = parse_price(raw)
        except (ValueError, OverflowError):
            return
        formatted = fmt_price(price)
        dpg.set_value(sender, formatted)
        self._vars[chest_type][item_name] = formatted

        name_lower = item_name.lower()
        synced_to: list[str] = []

        for other_ct, other_vars in self._vars.items():
            if other_ct == chest_type:
                continue
            for existing_name in other_vars:
                if existing_name.lower() == name_lower:
                    other_vars[existing_name] = formatted
                    other_tag = self._tag_for(other_ct, existing_name)
                    if dpg.does_item_exist(other_tag):
                        dpg.set_value(other_tag, formatted)
                    _, short = _chest_display(other_ct)
                    synced_to.append(short)

        sync = self._ids.get("sync_label")
        if sync and dpg.does_item_exist(sync):
            msg = f"'{item_name}' synced to: {', '.join(synced_to)}" if synced_to else f"'{item_name}' saved"
            dpg.configure_item(sync, default_value=msg)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self) -> None:
        from ui.tracker_tab import _show_modal

        all_prices: dict[str, dict[str, float]] = {}
        parse_errors: list[str] = []
        orphan_warnings: list[str] = []

        for ct, items in self._vars.items():
            allowed = {n.lower() for n in DEFAULT_ITEMS.get(ct, [])}
            prices: dict[str, float] = {}
            for item_name, price_str in items.items():
                tag = self._tag_for(ct, item_name)
                if dpg.does_item_exist(tag):
                    price_str = dpg.get_value(tag)
                    self._vars[ct][item_name] = price_str
                if item_name.lower() not in allowed:
                    orphan_warnings.append(f"  {ct}: '{item_name}'")
                    continue
                try:
                    prices[item_name] = parse_price(price_str)
                except (ValueError, OverflowError):
                    parse_errors.append(f"  {ct} -> {item_name}: '{price_str}'")
            all_prices[ct] = prices

        if parse_errors:
            _show_modal("Invalid Prices", "Fix these entries:\n\n" + "\n".join(parse_errors))
            return
        if orphan_warnings:
            _show_modal(
                "Unknown Items Skipped",
                "These items are not in chest_definitions.py and were NOT saved:\n\n" + "\n".join(orphan_warnings),
            )

        prices_config.save_all_prices(all_prices)
        self._on_prices_changed(all_prices)
        sync = self._ids.get("sync_label")
        if sync and dpg.does_item_exist(sync):
            dpg.configure_item(sync, default_value="All prices saved")

    # ------------------------------------------------------------------
    # Drop-rate refresh
    # ------------------------------------------------------------------

    def _on_refresh_click(self, sender: int, app_data: object, user_data: object) -> None:
        if isinstance(user_data, str):
            self._refresh_single_chest(user_data)

    def _refresh_single_chest(self, chest_type: str) -> None:
        _, short = _chest_display(chest_type)
        self._loading_chests.add(chest_type)
        self._render_cards()
        sync = self._ids.get("sync_label")
        if sync and dpg.does_item_exist(sync):
            dpg.configure_item(sync, default_value=f"Refreshing {short}...")
        threading.Thread(target=self._fetch_single_worker, args=(chest_type,), daemon=True).start()

    def _fetch_single_worker(self, chest_type: str) -> None:
        rates = db_handler.fetch_drop_rates(chest_type)
        avgs = db_handler.fetch_avg_quantities(chest_type)
        saved = prices_config.load_prices(chest_type)
        pl = {k.lower(): v for k, v in saved.items()}
        stats = db_handler.calculate_statistics(chest_type, pl)
        self._drop_rates[chest_type] = rates
        self._avg_qty[chest_type] = avgs
        self._chest_stats[chest_type] = stats
        self._loading_chests.discard(chest_type)
        dpg.split_frame()
        self._render_cards()
        self._update_avg_label(chest_type)
        _, short = _chest_display(chest_type)
        sync = self._ids.get("sync_label")
        if sync and dpg.does_item_exist(sync):
            dpg.configure_item(sync, default_value=f"{short} refreshed")

    def apply_drop_rates(
        self,
        all_rates: dict[str, dict[str, float]],
        all_stats: dict[str, db_handler.Stats] | None = None,
        all_avgs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        if all_stats:
            self._chest_stats = all_stats
        if all_avgs:
            self._avg_qty = all_avgs
        for ct, rates in all_rates.items():
            self._drop_rates[ct] = rates
            self._loading_chests.discard(ct)
        self._render_cards()
        for ct in self._chest_types:
            self._update_avg_label(ct)
        sync = self._ids.get("sync_label")
        if sync and dpg.does_item_exist(sync):
            dpg.configure_item(sync, default_value="Drop rates loaded")

    def set_loading(self, chest_types: list[str]) -> None:
        """Mark chest types as loading (shows spinner). Called from app startup."""
        for ct in chest_types:
            self._loading_chests.add(ct)
        self._render_cards()

    def _update_avg_label(self, chest_type: str) -> None:
        tag = self._ids.get(f"avg_{chest_type}")
        if not tag or not dpg.does_item_exist(tag):
            return
        stats = self._chest_stats.get(chest_type)
        if stats and stats.avg_revenue_per_chest > 0:
            text = f"avg {_fmt_k(stats.avg_revenue_per_chest)}  |  {stats.total_chests} chests"
        else:
            rates = self._drop_rates.get(chest_type, {})
            saved = prices_config.load_prices(chest_type)
            pl = {k.lower(): v for k, v in saved.items()}
            expected = sum((rates.get(n, 0.0) / 100.0) * pl.get(n.lower(), 0.0) for n in rates)
            text = f"est. avg {_fmt_k(expected)}" if expected > 0 else "avg: --"
        dpg.configure_item(tag, default_value=text)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, _sender: int | str, app_data: str) -> None:
        self._search_text = app_data
        self._render_cards()

    def _clear_search(self) -> None:
        self._search_text = ""
        tag = self._ids.get("search_input")
        if tag and dpg.does_item_exist(tag):
            dpg.set_value(tag, "")
        self._render_cards()

    def refresh_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        self._vars.clear()
        self._pinned.clear()
        self._load_all()
