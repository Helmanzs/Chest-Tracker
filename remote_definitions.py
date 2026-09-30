"""
remote_definitions.py
---------------------
Pulls chest_definitions.json from the Supabase storage bucket on every launch.

Resolution order
----------------
1. Supabase storage (fresh download)      -> saved to local cache on success
2. Local cache (chest_definitions_cache.json) if offline / download failed
3. None -> caller keeps the definitions bundled in chest_definitions.py

Expected JSON structure
-----------------------
{
  "chest_definitions": [{"name": ..., "display": ..., "color": "#rrggbb"}, ...],
  "default_items":     {"<chest name>": ["Item", ...], ...},
  "pattern_chests":    [{"name": ..., "required": ["Item", ...]}, ...],
  "bounty_tier_groups":{"<chest name>": ["<tier>", ...], ...}
}

Safe to import very early: only depends on config.py and the stdlib.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

DEFAULT_SUPABASE_URL = "https://wwgczilevfjyivjmgoia.supabase.co"
BUCKET = "config"
OBJECT_NAME = "chest_definitions.json"
CACHE_FILE = Path("chest_definitions_cache.json")
TIMEOUT_SECONDS = 5  # startup is blocked while this runs, keep it short


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────


def _validate(data: object) -> dict:
    """Raise ValueError if *data* isn't a usable definitions document."""
    if not isinstance(data, dict):
        raise ValueError("root must be an object")

    chests = data.get("chest_definitions")
    if not isinstance(chests, list) or not chests:
        raise ValueError("'chest_definitions' must be a non-empty list")
    for c in chests:
        if not isinstance(c, dict) or not all(
            isinstance(c.get(k), str) and c[k] for k in ("name", "display", "color")
        ):
            raise ValueError(f"bad chest entry: {c!r}")

    items = data.get("default_items")
    if not isinstance(items, dict):
        raise ValueError("'default_items' must be an object")
    for name, lst in items.items():
        if not isinstance(lst, list) or not all(isinstance(i, str) for i in lst):
            raise ValueError(f"default_items[{name!r}] must be a list of strings")

    patterns = data.get("pattern_chests", [])
    if not isinstance(patterns, list):
        raise ValueError("'pattern_chests' must be a list")
    for p in patterns:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("required"), list):
            raise ValueError(f"bad pattern chest: {p!r}")

    groups = data.get("bounty_tier_groups", {})
    if not isinstance(groups, dict) or not all(isinstance(v, list) for v in groups.values()):
        raise ValueError("'bounty_tier_groups' must map to lists")

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Download / cache
# ─────────────────────────────────────────────────────────────────────────────


def _get(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ChestTracker", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def _download() -> dict:
    """
    Try the public endpoint first (works without a key, e.g. on first launch),
    then the authenticated endpoint using the saved Supabase key (private bucket).
    A timestamp query param defeats CDN caching so edits show up immediately.
    """
    base = (config.load("supabase_url") or DEFAULT_SUPABASE_URL).rstrip("/")
    key = config.load("supabase_key")
    bust = f"?t={int(time.time())}"
    no_cache = {"Cache-Control": "no-cache"}

    attempts: list[tuple[str, dict[str, str]]] = [
        (f"{base}/storage/v1/object/public/{BUCKET}/{OBJECT_NAME}{bust}", no_cache),
    ]
    if key:
        attempts.append(
            (
                f"{base}/storage/v1/object/authenticated/{BUCKET}/{OBJECT_NAME}{bust}",
                {**no_cache, "apikey": key, "Authorization": f"Bearer {key}"},
            )
        )

    last_err: Exception | None = None
    for url, headers in attempts:
        try:
            raw = _get(url, headers)
            return _validate(json.loads(raw.decode("utf-8-sig")))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_err = exc
    raise RuntimeError(str(last_err) if last_err else "no download attempt made")


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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[definitions] cache unreadable: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def fetch_definitions() -> tuple[dict | None, str]:
    """
    Returns (data, status_message). *data* is None when neither the remote
    file nor a cache is available, in which case the caller should keep the
    bundled definitions.
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
