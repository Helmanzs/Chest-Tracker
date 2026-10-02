"""Entry point: enforces a single instance via a named Windows mutex, then runs the app."""

import sys

_ERROR_ALREADY_EXISTS = 183


def _show_already_running_window() -> None:
    import dearpygui.dearpygui as dpg

    dpg.create_context()
    dpg.create_viewport(title="Already Running", width=400, height=120)
    dpg.setup_dearpygui()
    with dpg.window(label="Already Running", no_close=True):
        dpg.add_text("Chest Tracker is already open.")
        dpg.add_text("Check your taskbar.")
        dpg.add_spacer(height=8)
        dpg.add_button(label="OK", callback=dpg.stop_dearpygui)
    dpg.set_primary_window(dpg.last_container(), True)
    dpg.show_viewport()
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()
    dpg.destroy_context()


def _acquire_single_instance_lock() -> object | None:
    """Return the mutex handle (keep it referenced), or exit if another instance runs."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, True, "ChestTrackerSingleInstanceMutex")
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            _show_already_running_window()
            sys.exit(0)
        return mutex
    except Exception as exc:
        print(f"[main] single-instance check failed: {exc}")
        return None


def main() -> None:
    _mutex = _acquire_single_instance_lock()  # noqa: F841 - must stay alive for the process lifetime

    from app import App

    App().run()


if __name__ == "__main__":
    main()
