"""
Pulls chest_definitions.json from the Supabase storage bucket on every launch.

Resolution order:
  1. Supabase storage (fresh download), saved to the local cache on success
  2. Local cache (chest_definitions_cache.json) if offline
  3. None, so the caller keeps the definitions bundled in chest_definitions.py

Expected JSON:
  {
    "chest_definitions":  [{"name", "display", "color"}, ...],
    "default_items":      {"<chest>": ["Item", ...], ...},
    "pattern_chests":     [{"name", "required": [...]}, ...],
    "bounty_tier_groups": {"<chest>": ["<tier>", ...], ...}
  }

Only depends on config.py and the stdlib, so it is safe to import early.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

BUCKET = "config"
OBJECT_NAME = "chest_definitions.json"
CACHE_FILE = Path("chest_definitions_cache.json")
TIMEOUT_SECONDS = 5  # startup blocks on this, keep it short


def _validate(data: object) -> dict:
    """Raise ValueError if *data* isn't a usable definitions document."""
    if not isinstance(data, dict):
        raise ValueError("root must be an object")

    chests = data.get("chest_definitions")
    if not isinstance(chests, list) or not chests:
        raise ValueError("'chest_definitions' must be a non-empty list")
    for chest in chests:
        if not isinstance(chest, dict) or not all(
            isinstance(chest.get(k), str) and chest[k] for k in ("name", "display", "color")
        ):
            raise ValueError(f"bad chest entry: {chest!r}")

    items = data.get("default_items")
    if not isinstance(items, dict):
        raise ValueError("'default_items' must be an object")
    for name, names in items.items():
        if not isinstance(names, list) or not all(isinstance(i, str) for i in names):
            raise ValueError(f"default_items[{name!r}] must be a list of strings")

    patterns = data.get("pattern_chests", [])
    if not isinstance(patterns, list):
        raise ValueError("'pattern_chests' must be a list")
    for pattern in patterns:
        if (
            not isinstance(pattern, dict)
            or not isinstance(pattern.get("name"), str)
            or not isinstance(pattern.get("required"), list)
        ):
            raise ValueError(f"bad pattern chest: {pattern!r}")

    groups = data.get("bounty_tier_groups", {})
    if not isinstance(groups, dict) or not all(isinstance(v, list) for v in groups.values()):
        raise ValueError("'bounty_tier_groups' must map to lists")

    return data


def _get(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ChestTracker", **headers})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def _download() -> dict:
    """
    Try the public endpoint first (works without a key, e.g. first launch),
    then the authenticated one using the saved key (private bucket).
    A timestamp query param defeats CDN caching.
    """
    base = (config.load("supabase_url") or config.DEFAULT_SUPABASE_URL).rstrip("/")
    key = config.load("supabase_key")
    bust = f"?t={int(time.time())}"
    no_cache = {"Cache-Control": "no-cache"}

    attempts = [(f"{base}/storage/v1/object/public/{BUCKET}/{OBJECT_NAME}{bust}", no_cache)]
    if key:
        attempts.append(
            (
                f"{base}/storage/v1/object/authenticated/{BUCKET}/{OBJECT_NAME}{bust}",
                {**no_cache, "apikey": key, "Authorization": f"Bearer {key}"},
            )
        )

    last_error: Exception | None = None
    for url, headers in attempts:
        try:
            return _validate(json.loads(_get(url, headers).decode("utf-8-sig")))
        except (urllib.error.URLError, OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else "no download attempt made")


def _save_cache(data: dict) -> None:
    tmp = CACHE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CACHE_FILE)  # atomic: never leaves a half-written cache
    except OSError as exc:
        print(f"[definitions] cache write error: {exc}")


def _load_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        return _validate(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        print(f"[definitions] cache unreadable: {exc}")
        return None


def fetch_definitions() -> tuple[dict | None, str]:
    """
    Return (data, status_message). *data* is None when neither the remote
    file nor a cache is available.
    """
    try:
        data = _download()
        _save_cache(data)
        return data, f"loaded from Supabase ({len(data['chest_definitions'])} chests)"
    except Exception as exc:
        print(f"[definitions] remote fetch failed: {exc}")

    cached = _load_cache()
    if cached is not None:
        return cached, f"Supabase unreachable - using cached copy ({len(cached['chest_definitions'])} chests)"

    return None, "Supabase unreachable and no cache - using bundled definitions"
