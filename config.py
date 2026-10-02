"""
Key-value user settings persisted to tracker_config.txt
(log path, supabase credentials, ...).

Chest definitions live in chest_definitions.py; prices in prices_config.py.
"""

from pathlib import Path

CONFIG_FILE = Path("tracker_config.txt")
DEFAULT_SUPABASE_URL = "https://wwgczilevfjyivjmgoia.supabase.co"


def _read_all() -> dict[str, str]:
    values: dict[str, str] = {}
    if not CONFIG_FILE.exists():
        return values
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key, _, value = stripped.partition("=")
                    values[key.strip()] = value.strip()
    except OSError as exc:
        print(f"[config] read error: {exc}")
    return values


def load(key: str, default: str = "") -> str:
    return _read_all().get(key, default)


def save(values: dict[str, str]) -> None:
    """Persist *values*, merging with the keys already on disk."""
    merged = {**_read_all(), **values}
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            for key, value in merged.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:
        print(f"[config] write error: {exc}")


def has_supabase_config() -> bool:
    url = load("supabase_url")
    key = load("supabase_key")
    return bool(url and key and "YOUR_" not in url and "YOUR_" not in key)


def save_supabase(url: str, key: str) -> None:
    save({"supabase_url": url, "supabase_key": key})
