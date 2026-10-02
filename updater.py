"""
Checks GitHub releases for a newer version and replaces the running exe.

    result = check_for_update(current_version="1.0.0")
    if result.update_available:
        download_and_replace(result, on_progress=..., on_complete=...)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from typing import Callable

GITHUB_API = "https://api.github.com/repos/Helmanzs/Chest-Tracker/releases/latest"
EXE_ASSET_NAME = "ChestTracker.exe"

ProgressCallback = Callable[[str], None]
CompleteCallback = Callable[[bool, str], None]


@dataclass
class UpdateResult:
    update_available: bool
    current_version: str = ""
    latest_version: str = ""
    download_url: str = ""
    release_notes: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------


def _parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' or '1.2.3' -> (1, 2, 3)."""
    try:
        return tuple(int(part) for part in tag.lstrip("v").strip().split("."))
    except ValueError:
        return (0,)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def check_for_update(current_version: str) -> UpdateResult:
    """Query the GitHub releases API. Safe to call from a background thread."""
    try:
        request = urllib.request.Request(
            GITHUB_API,
            headers={"User-Agent": "ChestTracker-Updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        latest_tag: str = data.get("tag_name", "")
        if not latest_tag:
            return UpdateResult(update_available=False, error="No tag found in release")

        download_url = next(
            (
                asset.get("browser_download_url", "")
                for asset in data.get("assets", [])
                if asset.get("name", "").lower() == EXE_ASSET_NAME.lower()
            ),
            "",
        )
        if not download_url:
            return UpdateResult(
                update_available=False,
                error=f"Asset '{EXE_ASSET_NAME}' not found in release {latest_tag}",
            )

        return UpdateResult(
            update_available=_is_newer(latest_tag, current_version),
            current_version=current_version,
            latest_version=latest_tag,
            download_url=download_url,
            release_notes=(data.get("body") or "")[:500],
        )
    except Exception as exc:
        return UpdateResult(update_available=False, error=str(exc))


# ---------------------------------------------------------------------------
# Download & replace
# ---------------------------------------------------------------------------


def download_and_replace(
    result: UpdateResult,
    on_progress: ProgressCallback | None = None,
    on_complete: CompleteCallback | None = None,
) -> None:
    """
    Download the new exe in a background thread, then launch a helper batch
    script that swaps it in once this process has exited (Windows only).
    """
    threading.Thread(
        target=_download_worker,
        args=(result, on_progress or (lambda _: None), on_complete or (lambda *_: None)),
        daemon=True,
    ).start()


def _download_worker(result: UpdateResult, progress: ProgressCallback, complete: CompleteCallback) -> None:
    try:
        progress(f"Downloading {EXE_ASSET_NAME} {result.latest_version}…")
        new_exe = _download_to_temp(result.download_url, progress)

        progress("Download complete. Preparing update…")
        _launch_replace_script(new_exe, _current_exe_path())

        complete(True, f"Update to {result.latest_version} downloaded — please close and reopen the app.")
    except Exception as exc:
        complete(False, f"Update failed: {exc}")


def _download_to_temp(url: str, progress: ProgressCallback) -> str:
    fd, path = tempfile.mkstemp(suffix=".exe", prefix="ChestTracker_new_")
    os.close(fd)

    with urllib.request.urlopen(url, timeout=60) as resp, open(path, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        while chunk := resp.read(65536):
            out.write(chunk)
            downloaded += len(chunk)
            if total:
                progress(f"Downloading… {int(downloaded / total * 100)}%")
    return path


def _current_exe_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    # Running from source: drop the new exe next to this script.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), EXE_ASSET_NAME)


def _launch_replace_script(new_exe: str, current_exe: str) -> None:
    """
    Write and start a batch script that waits for this PID to exit, then
    retries moving the new exe into place (the lock is released only after
    PyInstaller's cleanup). Progress is logged to chest_tracker_update.log.
    """
    pid = os.getpid()
    tmp = tempfile.gettempdir()
    bat_path = os.path.join(tmp, "chest_tracker_update.bat")
    log_path = os.path.join(tmp, "chest_tracker_update.log")

    lines = [
        "@echo off",
        f'echo Waiting for PID {pid} to exit... > "{log_path}"',
        ":wait_pid",
        f'tasklist /fi "PID eq {pid}" 2>nul | find /i "{pid}" >nul',
        "if not errorlevel 1 (",
        "    timeout /t 1 /nobreak >nul",
        "    goto wait_pid",
        ")",
        f'echo Process exited. Waiting 2s for file lock release... >> "{log_path}"',
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
        f'    echo FAILED to replace exe after %RETRIES% attempts >> "{log_path}"',
        "    goto end",
        ")",
        f'echo SUCCESS: replaced exe >> "{log_path}"',
        ":end",
        'del "%~f0"',
    ]
    with open(bat_path, "w", newline="") as fh:
        fh.write("\r\n".join(lines) + "\r\n")

    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        close_fds=True,
    )
