"""
config.py
---------
Thin key-value config persisted to tracker_config.txt.
Only stores user settings (log path, supabase credentials, mini position, etc.)

Chest definitions have moved to chest_definitions.py (static, app-bundled).
Item prices are managed by prices_config.py.

Format of tracker_config.txt
-----------------------------
log_path=C:/path/to/game.log
supabase_url=https://xxxx.supabase.co
supabase_key=your_key_here
mini_x=500
mini_y=900

No UI or business-logic imports – safe to import from anywhere.
"""

from pathlib import Path

CONFIG_FILE = Path("tracker_config.txt")

# ─────────────────────────────────────────────────────────────────────────────
# Low-level key-value helpers
# ─────────────────────────────────────────────────────────────────────────────


def load(key: str, default: str = "") -> str:
    """Return the stored value for *key*, or *default* if absent."""
    if not CONFIG_FILE.exists():
        return default
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("["):
                    continue
                if "=" in stripped:
                    k, _, v = stripped.partition("=")
                    if k.strip() == key:
                        return v.strip()
    except OSError as exc:
        print(f"[config] read error: {exc}")
    return default


def save(values: dict[str, str]) -> None:
    """Persist *values*, merging with any keys already on disk."""
    existing: dict[str, str] = {}

    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("["):
                        continue
                    if "=" in stripped and not stripped.startswith("#"):
                        k, _, v = stripped.partition("=")
                        existing[k.strip()] = v.strip()
        except OSError as exc:
            print(f"[config] read error before save: {exc}")

    existing.update(values)

    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            for k, v in existing.items():
                fh.write(f"{k}={v}\n")
    except OSError as exc:
        print(f"[config] write error: {exc}")


def load_all() -> dict[str, str]:
    """Return all key=value pairs from the config file."""
    result: dict[str, str] = {}
    if not CONFIG_FILE.exists():
        return result
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("["):
                    continue
                if "=" in stripped:
                    k, _, v = stripped.partition("=")
                    result[k.strip()] = v.strip()
    except OSError as exc:
        print(f"[config] load_all error: {exc}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Supabase key validation helpers
# ─────────────────────────────────────────────────────────────────────────────


def has_supabase_config() -> bool:
    """Return True if both supabase_url and supabase_key are set."""
    url = load("supabase_url")
    key = load("supabase_key")
    return bool(url and key and "YOUR_" not in url and "YOUR_" not in key)


def save_supabase(url: str, key: str) -> None:
    """Save Supabase credentials to tracker_config.txt."""
    save({"supabase_url": url, "supabase_key": key})
