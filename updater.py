"""
updater.py
----------
Checks GitHub releases for a newer version and downloads ChestTracker.exe
to replace the running executable.

Usage
-----
    from updater import check_for_update, UpdateResult

    result = check_for_update(current_version="1.0.0")
    if result.update_available:
        # show dialog, then:
        updater.download_and_replace(result, on_progress=callback)
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable

GITHUB_API = "https://api.github.com/repos/Helmanzs/Chest-Tracker/releases/latest"
EXE_ASSET_NAME = "ChestTracker.exe"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    update_available: bool
    current_version: str = ""
    latest_version: str = ""
    download_url: str = ""
    release_notes: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3)."""
    clean = tag.lstrip("v").strip()
    try:
        return tuple(int(x) for x in clean.split("."))
    except ValueError:
        return (0,)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


def check_for_update(current_version: str) -> UpdateResult:
    """
    Query the GitHub releases API and return an UpdateResult.
    Safe to call from a background thread.
    """
    try:
        import urllib.request
        import json

        req = urllib.request.Request(
            GITHUB_API,
            headers={"User-Agent": "ChestTracker-Updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        latest_tag: str = data.get("tag_name", "")
        release_notes: str = data.get("body", "")

        if not latest_tag:
            return UpdateResult(update_available=False, error="No tag found in release")

        # Find the exe asset
        download_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "").lower() == EXE_ASSET_NAME.lower():
                download_url = asset.get("browser_download_url", "")
                break

        if not download_url:
            return UpdateResult(
                update_available=False,
                error=f"Asset '{EXE_ASSET_NAME}' not found in release {latest_tag}",
            )

        available = _is_newer(latest_tag, current_version)
        return UpdateResult(
            update_available=available,
            current_version=current_version,
            latest_version=latest_tag,
            download_url=download_url,
            release_notes=release_notes[:500] if release_notes else "",
        )

    except Exception as exc:
        return UpdateResult(update_available=False, error=str(exc))


# ---------------------------------------------------------------------------
# Download & replace
# ---------------------------------------------------------------------------


def download_and_replace(
    result: UpdateResult,
    on_progress: Callable[[str], None] | None = None,
    on_complete: Callable[[bool, str], None] | None = None,
) -> None:
    """
    Download the new exe in a background thread, then schedule a replace
    on next launch using a helper batch script (Windows).

    on_progress(message)         — status updates
    on_complete(success, message) — final callback
    """
    threading.Thread(
        target=_download_worker,
        args=(result, on_progress, on_complete),
        daemon=True,
    ).start()


def _download_worker(
    result: UpdateResult,
    on_progress: Callable[[str], None] | None,
    on_complete: Callable[[bool, str], None] | None,
) -> None:
    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def complete(ok: bool, msg: str) -> None:
        if on_complete:
            on_complete(ok, msg)

    try:
        import urllib.request

        progress(f"Downloading {EXE_ASSET_NAME} {result.latest_version}…")

        # Download to a temp file
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".exe", prefix="ChestTracker_new_")
        os.close(tmp_fd)

        with urllib.request.urlopen(result.download_url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 65536
            with open(tmp_path, "wb") as f:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if total:
                        pct = int(downloaded / total * 100)
                        progress(f"Downloading… {pct}%")

        progress("Download complete. Preparing update…")

        # Get the path of the running exe
        if getattr(sys, "frozen", False):
            current_exe = sys.executable
        else:
            # Running as a script — put the new exe next to main.py
            current_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), EXE_ASSET_NAME)

        new_exe = tmp_path
        bat_path = os.path.join(tempfile.gettempdir(), "chest_tracker_update.bat")

        # Write a batch script that:
        # 1. Waits for this PID to fully exit
        # 2. Retries the move up to 15 times (exe lock released after PyInstaller cleanup)
        # 3. Logs result so we can diagnose failures
        pid = os.getpid()
        log_path = os.path.join(tempfile.gettempdir(), "chest_tracker_update.log")
        bat_lines = [
            "@echo off",
            f'echo Waiting for PID {pid} to exit... > "{log_path}"',
            ":wait_pid",
            f'tasklist /fi "PID eq {pid}" 2>nul | find /i "{pid}" >nul',
            "if not errorlevel 1 (",
            "    timeout /t 1 /nobreak >nul",
            "    goto wait_pid",
            ")",
            'echo Process exited. Waiting 2s for file lock release... >> "' + log_path + '"',
            "timeout /t 2 /nobreak >nul",
            "set RETRIES=0",
            ":retry_move",
            f'move /y "{new_exe}" "{current_exe}" >nul 2>&1',
            "if errorlevel 1 (",
            "    set /a RETRIES+=1",
            "    if %RETRIES% LSS 15 (",
            "        timeout /t 1 /nobreak >nul",
            "        goto retry_move",
            "    )",
            '    echo FAILED to replace exe after %RETRIES% attempts >> "' + log_path + '"',
            "    goto end",
            ")",
            f'echo SUCCESS: replaced exe >> "{log_path}"',
            ":end",
            'del "%~f0"',
        ]
        bat_content = "\r\n".join(bat_lines) + "\r\n"
        with open(bat_path, "w", newline="") as f:
            f.write(bat_content)

        import subprocess

        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
            close_fds=True,
        )

        complete(True, f"Update to {result.latest_version} downloaded — please close and reopen the app.")

    except Exception as exc:
        complete(False, f"Update failed: {exc}")
