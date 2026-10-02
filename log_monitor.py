"""
Tails a game log in a background thread and emits parsed events via callbacks.
No UI imports.

Two detection modes:
  1. Named chests   - the chest name appears in a log line.
  2. Pattern chests - no announcement; detected by matching item signatures
                      in a batch of loot sharing one timestamp.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable

from constants import IGNORED_ITEMS, LOOT_TIMEOUT, PATTERN_CHESTS, canonical_item_name, match_key

LogCallback = Callable[[str, str], None]  # (message, colour)
LootCallback = Callable[[int, str], None]  # (quantity, item_name)
ChestCallback = Callable[[str], None]  # (chest_name)
TimeoutCallback = Callable[[], None]
PatternCallback = Callable[[str, list[tuple[int, str]]], None]  # (chest_name, loot)

# Items a boss can drop directly. A named chest is only confirmed once a loot
# item NOT in this set arrives (compared lower-case).
_DIRECT_DROP_ITEMS = frozenset({"dexterity of the smith (chest)", "emblem chest"})

_RE_TIMESTAMP = re.compile(r"(\[.*?\] \[.*?\]):")
_RE_LOOT = re.compile(r"You receive (\d+) (.*?)\.")


class LogMonitor:
    def __init__(
        self,
        log_path: str,
        chest_types: dict[str, str],
        on_chest_detected: ChestCallback,
        on_loot_item: LootCallback,
        on_log: LogCallback,
        on_timeout: TimeoutCallback,
        on_pattern_chest: PatternCallback | None = None,
        loot_timeout: float = LOOT_TIMEOUT,
    ) -> None:
        self.log_path = log_path
        self.chest_types = chest_types
        self.loot_timeout = loot_timeout

        self._on_chest_detected = on_chest_detected
        self._on_loot_item = on_loot_item
        self._on_log = on_log
        self._on_timeout = on_timeout
        self._on_pattern_chest = on_pattern_chest

        self._running = False

        # Named-chest state
        self._awaiting_loot = False
        self._target_timestamp: str | None = None
        self._captured_loot: list[tuple[int, str]] = []
        self._last_loot_time: float | None = None
        self._pending_chest: str | None = None  # named, awaiting first real loot item

        # Pattern-detection buffer: all loot sharing one timestamp
        self._free_ts: str | None = None
        self._free_loot: list[tuple[int, str]] = []
        self._free_last_time: float | None = None

        pattern_names = {name for name, _ in PATTERN_CHESTS}
        # Named chests only; pattern chests are detected by loot signature.
        self._chest_keys = [(name, match_key(name)) for name in chest_types if name not in pattern_names]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def awaiting_loot(self) -> bool:
        return self._awaiting_loot

    @property
    def captured_loot(self) -> list[tuple[int, str]]:
        return list(self._captured_loot)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._tail_log, daemon=True).start()
        threading.Thread(target=self._timeout_monitor, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def start_new_chest(self) -> None:
        self._awaiting_loot = True
        self._clear_capture()

    def finalize(self) -> list[tuple[int, str]] | None:
        """Stop collecting and return the captured loot (None if not collecting)."""
        if not self._awaiting_loot:
            return None
        self._awaiting_loot = False
        loot = list(self._captured_loot)
        self._clear_capture()
        return loot

    def reset(self) -> None:
        self._awaiting_loot = False
        self._clear_capture()
        self._pending_chest = None

    def is_loot_stale(self) -> bool:
        """True when a chest's loot has been collected and nothing new arrived within the timeout."""
        return (
            self._awaiting_loot
            and bool(self._captured_loot)
            and self._target_timestamp is not None
            and self._last_loot_time is not None
            and time.time() - self._last_loot_time >= self.loot_timeout
        )

    def _clear_capture(self) -> None:
        self._target_timestamp = None
        self._captured_loot = []
        self._last_loot_time = None

    # ------------------------------------------------------------------
    # Line parsing
    # ------------------------------------------------------------------

    def _process_line(self, line: str) -> None:
        line_key = match_key(line)
        for chest_name, chest_key in self._chest_keys:
            if chest_key in line_key:
                # Don't fire yet: wait for a real loot item to rule out a boss direct drop.
                self._pending_chest = chest_name
                return

        ts_match = _RE_TIMESTAMP.search(line)
        loot_match = _RE_LOOT.search(line)
        if not ts_match or not loot_match:
            return

        timestamp = ts_match.group(1)
        qty_str, item = loot_match.groups()
        item = canonical_item_name(item.strip())
        if item.lower() in IGNORED_ITEMS:
            return
        qty = int(qty_str)

        self._buffer_for_pattern_detection(timestamp, qty, item)

        if self._pending_chest is not None and item.lower() not in _DIRECT_DROP_ITEMS:
            self._on_chest_detected(self._pending_chest)
            self._pending_chest = None

        if self._awaiting_loot:
            self._collect_named_loot(timestamp, qty, item)

    def _buffer_for_pattern_detection(self, timestamp: str, qty: int, item: str) -> None:
        if timestamp != self._free_ts:
            if self._free_loot:
                self._check_pattern_chest(self._free_loot)
            self._free_ts = timestamp
            self._free_loot = []
        self._free_loot.append((qty, item))
        self._free_last_time = time.time()

    def _collect_named_loot(self, timestamp: str, qty: int, item: str) -> None:
        if self._target_timestamp is None:
            self._target_timestamp = timestamp
            self._on_log(f"Loot timestamp locked: {timestamp}", "blue")

        if timestamp == self._target_timestamp:
            self._captured_loot.append((qty, item))
            self._last_loot_time = time.time()
            self._on_loot_item(qty, item)
        elif self._captured_loot:
            self._on_log("Timestamp changed – different event detected. Saving batch...", "orange")
            self._on_timeout()
        else:
            self._on_log("Timestamp changed before any loot collected. Resetting...", "gray")
            self.reset()

    def _check_pattern_chest(self, loot: list[tuple[int, str]]) -> None:
        if not self._on_pattern_chest:
            return
        names = {match_key(item) for _, item in loot}
        for chest_name, required in PATTERN_CHESTS:
            if required.issubset(names):
                self._on_log(f"[!] Pattern match: {chest_name}", "blue")
                self._on_pattern_chest(chest_name, loot)
                return

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _tail_log(self) -> None:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(0, os.SEEK_END)
                self._on_log("Monitoring log file...", "blue")
                while self._running:
                    line = fh.readline()
                    if line:
                        self._process_line(line.strip())
                    else:
                        time.sleep(0.1)
        except Exception as exc:
            self._on_log(f"Log monitoring error: {exc}", "red")

    def _timeout_monitor(self) -> None:
        while self._running:
            now = time.time()
            idle = self._free_last_time is not None and now - self._free_last_time >= self.loot_timeout

            if self.is_loot_stale():
                self._on_log(f"Loot collection timeout ({self.loot_timeout}s). Saving...", "orange")
                self._on_timeout()

            # Checked before the buffer flush below, which resets _free_last_time.
            if self._pending_chest is not None and not self._awaiting_loot and idle:
                self._on_log(
                    f"Skipped direct boss drop for '{self._pending_chest}' (no chest items).",
                    "gray",
                )
                self._pending_chest = None

            if self._free_loot and idle:
                self._check_pattern_chest(self._free_loot)
                self._free_loot = []
                self._free_ts = None
                self._free_last_time = None

            time.sleep(0.5)
