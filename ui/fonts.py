"""System font lookup for Dear PyGui."""

from __future__ import annotations

import os
import sys

_CANDIDATES = {
    "win32": [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
    ],
    "darwin": [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
    ],
    "linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ],
}


def find_system_font() -> str | None:
    """Path to the first available Unicode-capable TTF for this platform, or None."""
    platform = "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"
    return next((p for p in _CANDIDATES[platform] if os.path.isfile(p)), None)
