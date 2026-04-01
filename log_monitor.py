"""
log_monitor.py
--------------
Watches a log file in a background thread and emits parsed events via
callbacks.  Zero tkinter / UI imports – fully testable in isolation.

Two detection modes
-------------------
1. Named chests  — a chest-name string appears directly in the log line.
2. Pattern chests — no announcement; detected by matching item signatures
   in a loot batch (e.g. Monstrous Feather + Monstrous Claw → World Bounty).
"""

from __future__ import annotations

import os
import re
import time
import threading
from typing import Callable

from constants import IGNORED_ITEMS, LOOT_TIMEOUT, PATTERN_CHESTS

# Type aliases
LogCallback = Callable[[str, str], None]  # (message, colour)
LootCallback = Callable[[int, str], None]  # (quantity, item_name)
ChestCallback = Callable[[str], None]  # (chest_name)
TimeoutCallback = Callable[[], None]
PatternCallback = Callable[[str, list[tuple[int, str]]], None]  # (chest_name, loot)

_RE_TIMESTAMP = re.compile(r"(\[.*?\] \[.*?\]):")
_RE_LOOT = re.compile(r"You receive (\d+) (.*?)\.")


class LogMonitor:
    """
    Tails *log_path* in a daemon thread, parsing chest detections and
    loot drops, then delegates to caller-supplied callbacks.
    """

    def __init__(
        self,
        log_path: str,
        chest_types: dict[str, str],
        selected_chest: str,
        on_chest_detected: ChestCallback,
        on_loot_item: LootCallback,
        on_log: LogCallback,
        on_timeout: TimeoutCallback,
        on_pattern_chest: PatternCallback | None = None,
        loot_timeout: float = LOOT_TIMEOUT,
    ) -> None:
        self.log_path = log_path
        self.chest_types = chest_types
        self.selected_chest = selected_chest
        self.loot_timeout = loot_timeout

        self._on_chest_detected = on_chest_detected
        self._on_loot_item = on_loot_item
        self._on_log = on_log
        self._on_timeout = on_timeout
        self._on_pattern_chest = on_pattern_chest

        # Named-chest state
        self._running = False
        self._awaiting_loot = False
        self._target_timestamp: str | None = None
        self._captured_loot: list[tuple[int, str]] = []
        self._last_loot_time: float | None = None

        # Free-collection buffer for pattern detection (always running)
        self._free_ts: str | None = None
        self._free_loot: list[tuple[int, str]] = []
        self._free_last_time: float | None = None

        self._tail_thread: threading.Thread | None = None
        self._timeout_thread: threading.Thread | None = None
        # Cache pattern chest names so named-detection skips them
        self._pattern_names: frozenset[str] = frozenset(name for name, _ in PATTERN_CHESTS)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def captured_loot(self) -> list[tuple[int, str]]:
        return list(self._captured_loot)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tail_thread = threading.Thread(target=self._tail_log, daemon=True)
        self._timeout_thread = threading.Thread(target=self._timeout_monitor, daemon=True)
        self._tail_thread.start()
        self._timeout_thread.start()

    def stop(self) -> None:
        self._running = False

    def start_new_chest(self, chest_name: str) -> None:
        self._awaiting_loot = True
        self._target_timestamp = None
        self._captured_loot = []
        self._last_loot_time = None

    def finalize(self) -> list[tuple[int, str]] | None:
        if not self._awaiting_loot:
            return None
        self._awaiting_loot = False
        loot = list(self._captured_loot)
        self._captured_loot = []
        self._target_timestamp = None
        self._last_loot_time = None
        return loot

    def reset(self) -> None:
        self._awaiting_loot = False
        self._target_timestamp = None
        self._captured_loot = []
        self._last_loot_time = None

    # ------------------------------------------------------------------
    # Line parsing  (defined before the thread methods so linter is happy)
    # ------------------------------------------------------------------

    def _process_line(self, line: str) -> None:
        """Parse a single log line and fire the appropriate callbacks."""

        # --- Named chest detection (skip pattern-detected chests) ---
        for chest_name in self.chest_types:
            if chest_name in self._pattern_names:
                continue  # detected by loot signature, not log text
            if chest_name in line:
                self._on_chest_detected(chest_name)
                return

        # --- Require both timestamp and loot fields ---
        ts_match = _RE_TIMESTAMP.search(line)
        loot_match = _RE_LOOT.search(line)
        if not ts_match or not loot_match:
            return

        current_ts = ts_match.group(1)
        qty_str, item = loot_match.groups()
        item = item.strip()

        if item.lower() in IGNORED_ITEMS:
            return

        qty = int(qty_str)

        # --- Free buffer: always collect for pattern detection ---
        if self._free_ts is None or current_ts != self._free_ts:
            # Timestamp changed — flush previous buffer
            if self._free_loot and self._free_ts is not None:
                self._check_pattern_chest(self._free_loot)
            self._free_ts = current_ts
            self._free_loot = []
        self._free_loot.append((qty, item))
        self._free_last_time = time.time()

        # --- Named-chest loot collection ---
        if not self._awaiting_loot:
            return

        if self._target_timestamp is None:
            self._target_timestamp = current_ts
            self._on_log(f"Loot timestamp locked: {current_ts}", "blue")

        if current_ts == self._target_timestamp:
            self._captured_loot.append((qty, item))
            self._last_loot_time = time.time()
            self._on_loot_item(qty, item)
        else:
            if self._captured_loot:
                self._on_log("Timestamp changed – different event detected. Saving batch...", "orange")
                self._on_timeout()
            else:
                self._on_log("Timestamp changed before any loot collected. Resetting...", "gray")
                self.reset()

    def _check_pattern_chest(self, loot: list[tuple[int, str]]) -> None:
        """Check if *loot* matches any pattern chest and fire the callback."""
        if not self._on_pattern_chest or not PATTERN_CHESTS:
            return
        item_names_lower = {item.strip().lower() for _, item in loot}
        for chest_name, required in PATTERN_CHESTS:
            if required.issubset(item_names_lower):
                self._on_log(f"[!] Pattern match: {chest_name}", "blue")
                self._on_pattern_chest(chest_name, loot)
                return

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _tail_log(self) -> None:
        """Background thread: read new lines as they appear in the log file."""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(0, os.SEEK_END)
                self._on_log("Monitoring log file...", "blue")
                while self._running:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    self._process_line(line.strip())
        except Exception as exc:
            self._on_log(f"Log monitoring error: {exc}", "red")

    def _timeout_monitor(self) -> None:
        """Background thread: fires timeouts when loot collection goes quiet."""
        while self._running:
            now = time.time()

            # Named chest timeout
            if (
                self._awaiting_loot
                and self._last_loot_time is not None
                and self._target_timestamp is not None
                and self._captured_loot
                and now - self._last_loot_time >= self.loot_timeout
            ):
                self._on_log(f"Loot collection timeout ({self.loot_timeout}s). Saving...", "orange")
                self._on_timeout()

            # Free buffer timeout — flush and check for pattern match
            if (
                self._free_loot
                and self._free_last_time is not None
                and now - self._free_last_time >= self.loot_timeout
            ):
                self._check_pattern_chest(self._free_loot)
                self._free_loot = []
                self._free_ts = None
                self._free_last_time = None

            time.sleep(0.5)
