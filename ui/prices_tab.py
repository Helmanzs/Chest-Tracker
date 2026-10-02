"""
Prices tab: horizontally scrollable card panels, one per chest type.

Layout:
  card_area : child_window (horizontal scroll)
    card    : child_window, fixed size
      header band (chest name + Refresh)
      avg-revenue line (+ loading spinner)
      column headings
      item list : child_window, the only vertically scrolling element

Cards are rebuilt only when their structure changes (initial load, search,
pin/unpin, chest list change). Data refreshes update text in place.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Callable

import dearpygui.dearpygui as dpg

import db_handler
import prices_config
from chest_definitions import DEFAULT_ITEMS
from constants import CHEST_COLORS, CHEST_DISPLAY_NAMES
from ui.dialogs import show_modal
from ui.fonts import find_system_font
from utils import fmt_number, lower_keys

# ---------------------------------------------------------------------------
# Layout (pixels)
# ---------------------------------------------------------------------------

_W_STRIPE = 8
_W_NAME = 178
_W_DROP = 66
_W_AVG = 54
_W_PRICE = 114
_COLUMN_WIDTHS = (_W_STRIPE + 8, _W_NAME, _W_DROP, _W_AVG, _W_PRICE)

_CARD_W = sum(_COLUMN_WIDTHS) + 20
_HDR_H = 56
_AVG_H = 22
_HEAD_H = 22
_ROW_H = 22
_ROWS_H = _ROW_H * 20
_CARD_H = _HDR_H + _AVG_H + 4 + _HEAD_H + 4 + _ROWS_H + 16
_CARD_GAP = 10

_FG_ZERO = (105, 105, 105, 255)
_FG_NORMAL = (220, 220, 220, 255)
_FG_CHANCE = (148, 148, 148, 255)
_FG_MUTED = (160, 160, 160, 255)
_ROW_EVEN = (48, 48, 54, 255)
_ROW_ODD = (40, 40, 46, 255)
_COL_HDR_FG = (190, 190, 190, 255)
_STRIPE_PINNED = (243, 156, 18, 255)
_STRIPE_SHARED = (52, 152, 219, 255)

_font_large: int = 0


def _ensure_fonts() -> None:
    """Load the large header font once."""
    global _font_large
    if _font_large:
        return
    path = find_system_font()
    if path is None:
        print("[prices_tab] no TTF found for large header font")
        return
    try:
        registry = dpg.add_font_registry()
        _font_large = dpg.add_font(path, 22, parent=registry)  # type: ignore[assignment]
    except Exception as exc:
        print(f"[prices_tab] font load failed: {exc}")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_price(raw: str) -> float:
    """Parse '1 500', '1,500', '2k', '3kk', '1kkk' into a number."""
    s = raw.strip().replace(" ", "").replace(",", "").lower()
    if not s:
        return 0.0
    for suffix, multiplier in (("kkk", 1_000_000_000), ("kk", 1_000_000), ("k", 1_000)):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * multiplier
    return float(s)


def fmt_price(price: float) -> str:
    if price == int(price):
        return fmt_number(price)
    return f"{price:,.2f}".replace(",", " ")


def _safe_parse(raw: str) -> float:
    try:
        return parse_price(raw)
    except (ValueError, OverflowError):
        return 0.0


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    try:
        return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    except ValueError:
        return (85, 85, 85)


def _chest_display(chest_type: str) -> tuple[tuple[int, int, int], str]:
    """(header colour, short display name)."""
    rgb = _hex_to_rgb(CHEST_COLORS.get(chest_type, "#555555"))
    short = CHEST_DISPLAY_NAMES.get(chest_type, chest_type.replace("'s Chest", "").replace(" Chest", "").strip())
    return rgb, short


def _text_col_for_bg(rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (255, 255, 255, 255) if luminance < 140 else (20, 20, 20, 255)


def _chance_text(chance: float | None) -> str:
    if chance is None:
        return ""
    return "?" if chance == 0.0 else f"{chance:.1f}%"


def _avg_text(avg: float | None) -> str:
    return f"{avg:.1f}" if avg and avg > 0 else ""


def _build_chest_vars(chest_type: str) -> dict[str, str]:
    """{item: formatted price} for every default item of *chest_type* (0 when unset)."""
    saved = {k.lower(): v for k, v in prices_config.load_prices(chest_type).items()}
    return {
        item: fmt_price(saved[item.lower()]) if item.lower() in saved else "0"
        for item in DEFAULT_ITEMS.get(chest_type, [])
    }


def _input_tag(chest_type: str, item_name: str) -> str:
    def clean(value: str) -> str:
        return "".join(c if c.isalnum() or c == "_" else "_" for c in value)

    return f"pi_{clean(chest_type)[:18]}_{clean(item_name)[:28]}"


def _make_header_theme(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (r, g, b, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    return theme


def _make_header_button_theme(rgb: tuple[int, int, int], text_col: tuple[int, int, int, int]) -> int:
    r, g, b = (max(0, c - 35) for c in rgb)
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 200))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (max(0, r - 20), max(0, g - 20), max(0, b - 20), 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, text_col)
    return theme


def _add_row_table() -> int:
    """A header-less table with the five item-row columns."""
    table = dpg.add_table(
        header_row=False,
        borders_innerV=False,
        policy=dpg.mvTable_SizingFixedFit,
        pad_outerX=False,
    )
    for width in _COLUMN_WIDTHS:
        dpg.add_table_column(parent=table, width_fixed=True, init_width_or_weight=float(width))
    return table


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

        self._vars: dict[str, dict[str, str]] = {}  # chest -> item -> price text
        self._shared_items: set[str] = set()  # lower-case names present in several chests
        self._pinned: dict[str, list[str]] = {}
        self._drop_rates: dict[str, dict[str, float]] = {}
        self._avg_qty: dict[str, dict[str, float]] = {}
        self._chest_stats: dict[str, db_handler.Stats] = {}
        self._loading_chests: set[str] = set()
        self._search_text = ""

        self._ids: dict[str, int | str] = {}
        self._header_themes: dict[tuple[int, int, int], int] = {}
        self._button_themes: dict[tuple[int, int, int], int] = {}
        self._highlight_themes: dict[bool, int] = {}

        # In-place update targets, rebuilt along with the cards
        self._row_widgets: dict[tuple[str, str], dict[str, int | str]] = {}
        self._card_widgets: dict[str, dict[str, int | str]] = {}

        _ensure_fonts()
        self._build()
        self._load_all()

    # ------------------------------------------------------------------
    # Scaffold
    # ------------------------------------------------------------------

    def _build(self) -> None:
        with dpg.group(parent=self._parent):
            with dpg.group(horizontal=True):
                save_btn = dpg.add_button(label="Save All Prices", height=32, width=150, callback=self._save_all)
                with dpg.theme() as theme:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(dpg.mvThemeCol_Button, (46, 204, 113, 220))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 204, 113, 255))
                        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (27, 152, 79, 255))
                dpg.bind_item_theme(save_btn, theme)

                dpg.add_spacer(width=16)
                self._ids["sync_label"] = dpg.add_text("", color=(127, 140, 141, 255))
                dpg.add_spacer(width=20)
                dpg.add_text("Search:")
                self._ids["search_input"] = dpg.add_input_text(
                    label="", width=200, hint="filter items...", callback=self._on_search
                )
                dpg.add_button(label="X", width=26, callback=self._clear_search)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            self._ids["card_area"] = dpg.add_child_window(
                width=-1, height=_CARD_H + 22, horizontal_scrollbar=True, border=False
            )

    def _set_sync_message(self, text: str) -> None:
        label = self._ids.get("sync_label")
        if label and dpg.does_item_exist(label):
            dpg.configure_item(label, default_value=text)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        name_count: Counter[str] = Counter()
        for ct in self._chest_types:
            self._pinned[ct] = prices_config.load_pinned_items(ct)
            self._vars[ct] = _build_chest_vars(ct)
            name_count.update(name.lower() for name in self._vars[ct])
        self._shared_items = {name for name, count in name_count.items() if count > 1}
        self._render_cards()

    def refresh_chest_types(self, chest_types: list[str]) -> None:
        self._chest_types = chest_types
        self._vars.clear()
        self._pinned.clear()
        self._load_all()

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
        self._update_cards_inplace()
        self._set_sync_message("Drop rates loaded")

    def set_loading(self, chest_types: list[str]) -> None:
        """Show spinners for *chest_types* (cards built later pick up the state themselves)."""
        self._loading_chests.update(chest_types)
        for ct in chest_types:
            self._update_spinner(ct)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_cards(self) -> None:
        """Full rebuild, preserving horizontal scroll position."""
        area = self._ids.get("card_area")
        if not area or not dpg.does_item_exist(area):
            return

        scroll_x = dpg.get_x_scroll(area)
        dpg.delete_item(area, children_only=True)
        self._row_widgets.clear()
        self._card_widgets.clear()

        if not self._chest_types:
            return

        with dpg.group(parent=area, horizontal=True):
            for ct in self._chest_types:
                self._build_card(ct)
                dpg.add_spacer(width=_CARD_GAP)

        dpg.set_frame_callback(
            dpg.get_frame_count() + 1,
            callback=lambda: dpg.set_x_scroll(area, scroll_x) if dpg.does_item_exist(area) else None,
        )

    def _update_cards_inplace(self) -> None:
        """Refresh drop%, avg qty, avg label and spinner text without rebuilding cards."""
        for ct in self._chest_types:
            self._update_avg_label(ct)
            self._update_spinner(ct)
            rates = self._drop_rates.get(ct, {})
            avgs = self._avg_qty.get(ct, {})
            for item_name in self._vars.get(ct, {}):
                widgets = self._row_widgets.get((ct, item_name), {})
                for key, text in (
                    ("drop_text", _chance_text(rates.get(item_name))),
                    ("avg_text", _avg_text(avgs.get(item_name))),
                ):
                    tag = widgets.get(key)
                    if tag and dpg.does_item_exist(tag):
                        dpg.configure_item(tag, default_value=text)

    def _update_spinner(self, chest_type: str) -> None:
        spinner = self._card_widgets.get(chest_type, {}).get("spinner")
        if spinner and dpg.does_item_exist(spinner):
            if chest_type in self._loading_chests:
                dpg.show_item(spinner)
            else:
                dpg.hide_item(spinner)

    def _update_avg_label(self, chest_type: str) -> None:
        tag = self._card_widgets.get(chest_type, {}).get("avg_label")
        if not tag or not dpg.does_item_exist(tag):
            return

        stats = self._chest_stats.get(chest_type)
        if stats and stats.avg_revenue_per_chest > 0:
            text = self._avg_summary(stats)
        else:
            rates = self._drop_rates.get(chest_type, {})
            prices = lower_keys(prices_config.load_prices(chest_type))
            expected = sum(rates.get(n, 0.0) / 100.0 * prices.get(n.lower(), 0.0) for n in rates)
            text = f"est. avg {fmt_number(expected)}" if expected > 0 else "avg: --"
        dpg.configure_item(tag, default_value=text)

    @staticmethod
    def _avg_summary(stats: db_handler.Stats) -> str:
        return f"avg {fmt_number(stats.avg_revenue_per_chest)}  |  {stats.total_chests} chests"

    # ------------------------------------------------------------------
    # Card
    # ------------------------------------------------------------------

    def _build_card(self, chest_type: str) -> None:
        rgb, short = _chest_display(chest_type)
        text_col = _text_col_for_bg(rgb)
        header_theme = self._header_themes.setdefault(rgb, _make_header_theme(rgb))
        button_theme = self._button_themes.setdefault(rgb, _make_header_button_theme(rgb, text_col))

        self._card_widgets[chest_type] = {}

        with dpg.child_window(width=_CARD_W, height=_CARD_H, border=True, no_scrollbar=True):
            self._build_card_header(chest_type, short, text_col, header_theme, button_theme)
            self._build_avg_line(chest_type)

            dpg.add_separator()
            self._build_heading_row()
            dpg.add_separator()

            with dpg.child_window(width=_CARD_W - 4, height=-1, border=False):
                self._build_item_rows(chest_type)

    def _build_card_header(
        self,
        chest_type: str,
        short: str,
        text_col: tuple[int, int, int, int],
        header_theme: int,
        button_theme: int,
    ) -> None:
        header = dpg.add_child_window(width=_CARD_W - 4, height=_HDR_H, border=False, no_scrollbar=True)
        dpg.bind_item_theme(header, header_theme)

        button_w = 80
        text_col_w = _CARD_W - 4 - button_w - 4

        with dpg.group(parent=header):
            dpg.add_spacer(height=12)
            table = dpg.add_table(
                header_row=False, borders_innerV=False, policy=dpg.mvTable_SizingFixedFit, pad_outerX=False
            )
            dpg.add_table_column(parent=table, width_fixed=True, init_width_or_weight=float(text_col_w))
            dpg.add_table_column(parent=table, width_fixed=True, init_width_or_weight=float(button_w))
            with dpg.table_row(parent=table):
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=10)
                    label = dpg.add_text(short, color=text_col)
                    if _font_large:
                        dpg.bind_item_font(label, _font_large)
                refresh = dpg.add_button(
                    label="Refresh",
                    width=button_w,
                    height=28,
                    user_data=chest_type,
                    callback=self._on_refresh_click,
                )
                dpg.bind_item_theme(refresh, button_theme)

    def _build_avg_line(self, chest_type: str) -> None:
        stats = self._chest_stats.get(chest_type)
        text = self._avg_summary(stats) if stats and stats.avg_revenue_per_chest > 0 else "avg: --"

        with dpg.group(horizontal=True):
            self._card_widgets[chest_type]["avg_label"] = dpg.add_text(text, color=_FG_MUTED, indent=6)
            dpg.add_spacer(width=8)
            spinner = dpg.add_loading_indicator(
                style=1,
                radius=3.5,
                speed=2.5,
                thickness=2.0,
                circle_count=6,
                color=_FG_MUTED,
                secondary_color=(50, 50, 58, 255),
            )
            self._card_widgets[chest_type]["spinner"] = spinner
            if chest_type not in self._loading_chests:
                dpg.hide_item(spinner)

    def _build_heading_row(self) -> None:
        table = _add_row_table()
        with dpg.table_row(parent=table):
            dpg.add_text("")
            for heading in ("Item", "Drop%", "Avg", "Price"):
                dpg.add_text(heading, color=_COL_HDR_FG)

    def _build_item_rows(self, chest_type: str) -> None:
        pinned, specific, shared = self._sorted_groups(chest_type)
        rates = self._drop_rates.get(chest_type, {})
        avgs = self._avg_qty.get(chest_type, {})

        row_idx = 0
        for name, price in pinned:
            self._build_row(chest_type, name, price, rates, avgs, row_idx, is_pinned=True)
            row_idx += 1
        for name, price in specific:
            self._build_row(chest_type, name, price, rates, avgs, row_idx)
            row_idx += 1
        if shared:
            dpg.add_text("-- Shared items --", color=(100, 100, 100, 255), indent=6)
        for name, price in shared:
            self._build_row(chest_type, name, price, rates, avgs, row_idx, is_shared=True)
            row_idx += 1

        if not (pinned or specific or shared):
            dpg.add_spacer(height=10)
            dpg.add_text("No matches" if self._search_text else "No items", color=_FG_MUTED, indent=8)

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
        row_bg = _ROW_EVEN if row_idx % 2 == 0 else _ROW_ODD
        fg = _FG_ZERO if _safe_parse(price_str) == 0.0 else _FG_NORMAL
        stripe = _STRIPE_PINNED if is_pinned else _STRIPE_SHARED if is_shared else row_bg
        input_tag = _input_tag(chest_type, item_name)

        table = _add_row_table()
        with dpg.table_row(parent=table):
            dpg.add_color_button(default_value=stripe, width=_W_STRIPE, height=14, no_tooltip=True, enabled=False)

            name_label = dpg.add_text(item_name[:26], color=fg)
            self._add_pin_menu(name_label, chest_type, item_name)

            drop_tag = dpg.add_text(_chance_text(drop_rates.get(item_name)), color=_FG_CHANCE)
            avg_tag = dpg.add_text(_avg_text(avg_qty.get(item_name)), color=_FG_CHANCE)

            if dpg.does_item_exist(input_tag):
                dpg.delete_item(input_tag)
            dpg.add_input_text(
                tag=input_tag,
                default_value=price_str,
                width=_W_PRICE - 4,
                label="",
                user_data=(chest_type, item_name),
                callback=self._on_price_change,
            )
            with dpg.item_handler_registry() as registry:
                dpg.add_item_activated_handler(
                    user_data=(chest_type, item_name),
                    callback=self._on_price_focus_in,
                )
                dpg.add_item_deactivated_handler(
                    user_data=(chest_type, item_name, input_tag),
                    callback=self._on_price_focus_out,
                )
            dpg.bind_item_handler_registry(input_tag, registry)

        dpg.highlight_table_row(table, 0, row_bg)
        self._row_widgets[(chest_type, item_name)] = {"drop_text": drop_tag, "avg_text": avg_tag}

    def _add_pin_menu(self, anchor: int | str, chest_type: str, item_name: str) -> None:
        is_pinned = item_name.lower() in (p.lower() for p in self._pinned.get(chest_type, []))
        label = f"Unpin '{item_name[:20]}'" if is_pinned else f"Pin '{item_name[:20]}' to top"
        with dpg.popup(parent=anchor, mousebutton=dpg.mvMouseButton_Right):
            dpg.add_menu_item(label=label, user_data=(chest_type, item_name), callback=self._on_pin_click)

    def _sorted_groups(
        self, chest_type: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
        """(pinned in pin order, chest-specific by price desc, shared by price desc), after search filtering."""
        query = self._search_text.strip().lower()
        pinned_lower = [p.lower() for p in self._pinned.get(chest_type, [])]

        pinned: list[tuple[str, str]] = []
        specific: list[tuple[str, str]] = []
        shared: list[tuple[str, str]] = []

        for name, price in self._vars.get(chest_type, {}).items():
            lower = name.lower()
            if query and query not in lower:
                continue
            if lower in pinned_lower:
                pinned.append((name, price))
            elif lower in self._shared_items:
                shared.append((name, price))
            else:
                specific.append((name, price))

        pinned.sort(key=lambda kv: pinned_lower.index(kv[0].lower()))
        specific.sort(key=lambda kv: -_safe_parse(kv[1]))
        shared.sort(key=lambda kv: -_safe_parse(kv[1]))
        return pinned, specific, shared

    # ------------------------------------------------------------------
    # Pin / unpin
    # ------------------------------------------------------------------

    def _on_pin_click(self, sender: int, app_data: object, user_data: object) -> None:
        if isinstance(user_data, tuple) and len(user_data) == 2:
            self._toggle_pin(*user_data)

    def _toggle_pin(self, chest_type: str, item_name: str) -> None:
        lower = item_name.lower()
        pinned = list(self._pinned.get(chest_type, []))
        if lower in (p.lower() for p in pinned):
            pinned = [p for p in pinned if p.lower() != lower]
        else:
            pinned.append(item_name)
        self._pinned[chest_type] = pinned
        prices_config.save_pinned_items(chest_type, pinned)
        self._render_cards()

    # ------------------------------------------------------------------
    # Price editing & cross-chest sync
    # ------------------------------------------------------------------

    def _siblings(self, chest_type: str, item_name: str) -> list[tuple[str, str]]:
        """(chest, item) pairs for the same item (case-insensitive) in all OTHER chests."""
        lower = item_name.lower()
        return [
            (other_ct, name)
            for other_ct, items in self._vars.items()
            if other_ct != chest_type
            for name in items
            if name.lower() == lower
        ]

    def _sibling_tags(self, chest_type: str, item_name: str) -> list[str]:
        tags = (_input_tag(ct, name) for ct, name in self._siblings(chest_type, item_name))
        return [tag for tag in tags if dpg.does_item_exist(tag)]

    def _on_price_change(self, sender: int, app_data: str, user_data: object) -> None:
        """Every keystroke: mirror the raw text into sibling fields."""
        if not isinstance(user_data, tuple) or len(user_data) != 2:
            return
        raw = dpg.get_value(sender)
        for tag in self._sibling_tags(*user_data):
            dpg.set_value(tag, raw)

    def _on_price_focus_in(self, sender: int, app_data: object, user_data: object) -> None:
        if isinstance(user_data, tuple) and len(user_data) == 2:
            self._highlight(self._sibling_tags(*user_data), active=True)

    def _on_price_focus_out(self, sender: int, app_data: object, user_data: object) -> None:
        if not isinstance(user_data, tuple) or len(user_data) != 3:
            return
        chest_type, item_name, input_tag = user_data
        if dpg.does_item_exist(input_tag):
            self._commit(chest_type, item_name, input_tag)
        self._highlight(self._sibling_tags(chest_type, item_name), active=False)

    def _highlight(self, tags: list[str], active: bool) -> None:
        if active not in self._highlight_themes:
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvInputText):
                    colour = (90, 80, 30, 255) if active else (50, 50, 58, 255)
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, colour)
            self._highlight_themes[active] = theme
        for tag in tags:
            dpg.bind_item_theme(tag, self._highlight_themes[active])

    def _commit(self, chest_type: str, item_name: str, input_tag: int | str) -> None:
        """Normalise the typed price and push it to the same item in other chests."""
        try:
            price = parse_price(dpg.get_value(input_tag))
        except (ValueError, OverflowError):
            return

        formatted = fmt_price(price)
        dpg.set_value(input_tag, formatted)
        self._vars[chest_type][item_name] = formatted

        synced_to: list[str] = []
        for other_ct, other_name in self._siblings(chest_type, item_name):
            self._vars[other_ct][other_name] = formatted
            tag = _input_tag(other_ct, other_name)
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, formatted)
            synced_to.append(_chest_display(other_ct)[1])

        if synced_to:
            self._set_sync_message(f"'{item_name}' synced to: {', '.join(synced_to)}")
        else:
            self._set_sync_message(f"'{item_name}' saved")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_all(self) -> None:
        all_prices: dict[str, dict[str, float]] = {}
        parse_errors: list[str] = []
        orphans: list[str] = []

        for ct, items in self._vars.items():
            allowed = {n.lower() for n in DEFAULT_ITEMS.get(ct, [])}
            prices: dict[str, float] = {}
            for item_name in items:
                tag = _input_tag(ct, item_name)
                if dpg.does_item_exist(tag):
                    items[item_name] = dpg.get_value(tag)
                price_str = items[item_name]

                if item_name.lower() not in allowed:
                    orphans.append(f"  {ct}: '{item_name}'")
                    continue
                try:
                    prices[item_name] = parse_price(price_str)
                except (ValueError, OverflowError):
                    parse_errors.append(f"  {ct} -> {item_name}: '{price_str}'")
            all_prices[ct] = prices

        if parse_errors:
            show_modal("Invalid Prices", "Fix these entries:\n\n" + "\n".join(parse_errors))
            return
        if orphans:
            show_modal(
                "Unknown Items Skipped",
                "These items are not in chest_definitions.py and were NOT saved:\n\n" + "\n".join(orphans),
            )

        prices_config.save_all_prices(all_prices)
        self._on_prices_changed(all_prices)
        self._set_sync_message("All prices saved")

    # ------------------------------------------------------------------
    # Per-chest drop-rate refresh
    # ------------------------------------------------------------------

    def _on_refresh_click(self, sender: int, app_data: object, user_data: object) -> None:
        if isinstance(user_data, str):
            self._refresh_single_chest(user_data)

    def _refresh_single_chest(self, chest_type: str) -> None:
        self._loading_chests.add(chest_type)
        self._update_spinner(chest_type)
        self._set_sync_message(f"Refreshing {_chest_display(chest_type)[1]}...")
        threading.Thread(target=self._fetch_single_worker, args=(chest_type,), daemon=True).start()

    def _fetch_single_worker(self, chest_type: str) -> None:
        self._drop_rates[chest_type] = db_handler.fetch_drop_rates(chest_type)
        self._avg_qty[chest_type] = db_handler.fetch_avg_quantities(chest_type)
        prices = lower_keys(prices_config.load_prices(chest_type))
        self._chest_stats[chest_type] = db_handler.calculate_statistics(chest_type, prices)
        self._loading_chests.discard(chest_type)

        dpg.split_frame()
        self._update_cards_inplace()
        self._set_sync_message(f"{_chest_display(chest_type)[1]} refreshed")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, _sender: int | str, app_data: str) -> None:
        self._search_text = app_data
        self._render_cards()

    def _clear_search(self) -> None:
        self._search_text = ""
        search = self._ids.get("search_input")
        if search and dpg.does_item_exist(search):
            dpg.set_value(search, "")
        self._render_cards()
