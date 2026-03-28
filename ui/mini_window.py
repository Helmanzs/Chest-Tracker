"""
ui/mini_window.py
-----------------
Borderless, always-on-top overlay that shows live tracking status.
Owns its own Toplevel widget and exposes a single update() method.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import config


class MiniWindow:
    """
    Small HUD overlay window.

    Parameters
    ----------
    root        : parent Tk root
    on_close    : called when the window is destroyed (lets the parent
                  toggle the button label back)
    """

    _WIDTH = 450
    _HEIGHT = 30
    _BG = "#1a1a1a"

    def __init__(self, root: tk.Tk, on_close: Callable[[], None]) -> None:
        self._root = root
        self._on_close = on_close
        self._win: tk.Toplevel | None = None

        # Widgets updated via update()
        self._status_indicator: tk.Label | None = None
        self._status_text: tk.Label | None = None
        self._expensive_item: tk.Label | None = None
        self._revenue_label: tk.Label | None = None

        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        win = tk.Toplevel(self._root)
        win.title("Chest Tracker")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close)

        x, y = self._load_position()
        win.geometry(f"{self._WIDTH}x{self._HEIGHT}+{x}+{y}")

        # ── Main frame ──────────────────────────────────────────────
        main = tk.Frame(win, bg=self._BG, relief=tk.RAISED, bd=1)
        main.pack(fill=tk.BOTH, expand=True)
        main.bind("<Button-1>", self._start_drag)
        main.bind("<B1-Motion>", self._do_drag)
        main.bind("<ButtonRelease-1>", self._save_position)

        content = tk.Frame(main, bg=self._BG)
        content.pack(fill=tk.BOTH, expand=True, padx=6, pady=5)

        # ── Status indicator (left) ──────────────────────────────────
        status_frame = tk.Frame(content, bg=self._BG)
        status_frame.pack(side=tk.LEFT, padx=(0, 10))

        self._status_indicator = tk.Label(status_frame, text="●", font=("Arial", 10), fg="#95a5a6", bg=self._BG)
        self._status_indicator.pack(side=tk.LEFT, padx=(0, 3))

        self._status_text = tk.Label(status_frame, text="READY", font=("Arial", 7, "bold"), fg="#95a5a6", bg=self._BG)
        self._status_text.pack(side=tk.LEFT)

        # ── Separator ───────────────────────────────────────────────
        tk.Label(content, text="|", font=("Arial", 9), fg="#444444", bg=self._BG).pack(side=tk.LEFT, padx=(0, 8))

        # ── Top item (centre) ────────────────────────────────────────
        tk.Label(content, text="TOP:", font=("Arial", 7, "bold"), fg="#7f8c8d", bg=self._BG).pack(
            side=tk.LEFT, padx=(0, 4)
        )

        self._expensive_item = tk.Label(content, text="-", font=("Arial", 8), fg="#f39c12", bg=self._BG)
        self._expensive_item.pack(side=tk.LEFT)

        # ── Revenue (right) ──────────────────────────────────────────
        revenue_frame = tk.Frame(content, bg=self._BG)
        revenue_frame.pack(side=tk.RIGHT)

        self._revenue_label = tk.Label(revenue_frame, text="N/A", font=("Arial", 8, "bold"), fg="#2ecc71", bg=self._BG)
        self._revenue_label.pack(side=tk.RIGHT)

        tk.Label(
            revenue_frame,
            text="REVENUE/CHEST:",
            font=("Arial", 7, "bold"),
            fg="#7f8c8d",
            bg=self._BG,
        ).pack(side=tk.RIGHT, padx=(10, 4))

        self._win = win

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(
        self,
        is_running: bool,
        most_expensive: tuple[str, float],
        avg_revenue: float,
    ) -> None:
        """Refresh all labels. Safe to call from any thread via root.after."""
        if self._win is None:
            return
        try:
            # Status dot
            colour = "#2ecc71" if is_running else "#95a5a6"
            label = "LIVE" if is_running else "READY"
            self._status_indicator.config(fg=colour)  # type: ignore[union-attr]
            self._status_text.config(text=label, fg=colour)  # type: ignore[union-attr]

            # Most-expensive item
            item_name, item_value = most_expensive
            if item_value > 0:
                display = item_name[:27] + "..." if len(item_name) > 30 else item_name
                self._expensive_item.config(text=display)  # type: ignore[union-attr]
            else:
                self._expensive_item.config(text="-")  # type: ignore[union-attr]

            # Average revenue
            if avg_revenue > 0:
                text = f"{avg_revenue:,.0f}".replace(",", " ")
                self._revenue_label.config(text=text)  # type: ignore[union-attr]
            else:
                self._revenue_label.config(text="N/A")  # type: ignore[union-attr]

        except Exception as exc:
            print(f"[mini_window] update error: {exc}")

    def close(self) -> None:
        """Destroy the window and notify the parent."""
        if self._win is not None:
            self._win.destroy()
            self._win = None
        self._on_close()

    # ------------------------------------------------------------------
    # Drag handling
    # ------------------------------------------------------------------

    def _start_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self._win:
            self._win._drag_x = event.x  # type: ignore[attr-defined]
            self._win._drag_y = event.y  # type: ignore[attr-defined]

    def _do_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self._win is None:
            return
        dx = event.x - self._win._drag_x  # type: ignore[attr-defined]
        dy = event.y - self._win._drag_y  # type: ignore[attr-defined]
        x = self._win.winfo_x() + dx
        y = self._win.winfo_y() + dy
        self._win.geometry(f"+{x}+{y}")

    def _save_position(self, event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        if self._win is None:
            return
        config.save(
            {
                "mini_x": str(self._win.winfo_x()),
                "mini_y": str(self._win.winfo_y()),
            }
        )

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def _load_position(self) -> tuple[int, int]:
        raw_x = config.load("mini_x")
        raw_y = config.load("mini_y")
        try:
            if raw_x and raw_y:
                return int(raw_x), int(raw_y)
        except (ValueError, TypeError):
            pass
        # Default: bottom-centre of screen
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        return (sw - self._WIDTH) // 2, sh - self._HEIGHT - 100
