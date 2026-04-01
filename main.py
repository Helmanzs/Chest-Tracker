"""
main.py
-------
Entry point. Enforces single instance via a named Windows mutex,
then boots Tk and hands off to App.
"""

import sys
import tkinter as tk
from app import App


def _acquire_single_instance_lock() -> object | None:
    """
    On Windows, create a named mutex. Returns the mutex handle on success,
    or None/exits if another instance is already running.
    On non-Windows platforms, returns a sentinel True (no-op).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        mutex = kernel32.CreateMutexW(None, True, "ChestTrackerSingleInstanceMutex")
        last_error = kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            import tkinter.messagebox as mb

            root = tk.Tk()
            root.withdraw()
            mb.showwarning(
                "Already Running",
                "Chest Tracker is already open.\nCheck your taskbar or system tray.",
            )
            root.destroy()
            sys.exit(0)
        return mutex  # keep reference alive for process lifetime
    except Exception as exc:
        print(f"[main] single-instance check failed: {exc}")
        return True


def main() -> None:
    _mutex = _acquire_single_instance_lock()  # noqa: F841 — must stay in scope
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
