"""
main.py  (Dear PyGui port)
---------------------------
Entry point. Enforces single instance via a named Windows mutex,
then boots Dear PyGui and hands off to App.
"""

import sys


def _acquire_single_instance_lock() -> object | None:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, True, "ChestTrackerSingleInstanceMutex")
        last_error = kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            import dearpygui.dearpygui as dpg

            dpg.create_context()
            dpg.create_viewport(title="Already Running", width=400, height=120)
            dpg.setup_dearpygui()
            with dpg.window(label="Already Running", no_close=True):
                dpg.add_text("Chest Tracker is already open.")
                dpg.add_text("Check your taskbar or system tray.")
                dpg.add_spacer(height=8)
                dpg.add_button(
                    label="OK",
                    callback=lambda: dpg.stop_dearpygui(),
                )
            dpg.set_primary_window(dpg.last_container(), True)
            dpg.show_viewport()
            while dpg.is_dearpygui_running():
                dpg.render_dearpygui_frame()
            dpg.destroy_context()
            sys.exit(0)
        return mutex
    except Exception as exc:
        print(f"[main] single-instance check failed: {exc}")
        return True


def main() -> None:
    _mutex = _acquire_single_instance_lock()  # noqa: F841

    from app import App

    application = App()
    application.run()


if __name__ == "__main__":
    main()
