"""
ui/setup_dialog.py
------------------
Dialog shown on first launch (or when Supabase key is missing/invalid).
Prompts the user for their Supabase key, validates it, and saves it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable


class SetupDialog(tk.Toplevel):
    """
    Modal dialog that asks the user for their Supabase access key.
    The URL is hardcoded (provided by the developer).

    Parameters
    ----------
    parent          : root Tk window
    supabase_url    : the fixed URL baked into the app
    on_success      : called with (url, key) when validation passes
    on_cancel       : called if the user closes without a valid key
    """

    # The Supabase URL is fixed — users only need the key
    SUPABASE_URL = "https://wwgczilevfjyivjmgoia.supabase.co"

    def __init__(
        self,
        parent: tk.Tk,
        on_success: Callable[[str, str], None],
        on_cancel: Callable[[], None],
        existing_key: str = "",
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self._on_success = on_success
        self._on_cancel = on_cancel

        self.title("Chest Tracker — Setup")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.attributes("-topmost", True)

        self._key_var = tk.StringVar(value=existing_key)
        self._status_var = tk.StringVar()
        self._build()

        # Center on screen after widgets are built so geometry is accurate
        self.update_idletasks()
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        x = (sw - 480) // 2
        y = (sh - 280) // 2
        self.geometry(f"480x280+{x}+{y}")

        self.lift()
        self.grab_set()
        self.focus_force()

    def _build(self) -> None:
        tk.Label(
            self,
            text="Welcome to Chest Tracker",
            font=("Arial", 14, "bold"),
        ).pack(pady=(20, 4))

        tk.Label(
            self,
            text="Please enter your access key to connect to the database.",
            font=("Arial", 9),
            fg="#555",
        ).pack(pady=(0, 12))

        form = tk.Frame(self)
        form.pack(fill=tk.X, padx=20, pady=6)

        tk.Label(form, text="Access Key:", font=("Arial", 10, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", pady=4
        )
        key_entry = tk.Entry(form, textvariable=self._key_var, width=44, show="")
        key_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        key_entry.focus_set()
        form.columnconfigure(1, weight=1)

        # Show/hide toggle
        self._show_key = tk.BooleanVar(value=True)
        tk.Checkbutton(
            form,
            text="Show key",
            variable=self._show_key,
            command=lambda: key_entry.config(show="" if self._show_key.get() else "●"),
        ).grid(row=1, column=1, sticky="w", padx=(8, 0))

        self._status_lbl = tk.Label(self, textvariable=self._status_var, font=("Arial", 9), fg="red")
        self._status_lbl.pack(pady=(4, 0))

        btn_row = tk.Frame(self)
        btn_row.pack(pady=(10, 16))

        self._connect_btn = tk.Button(
            btn_row,
            text="Connect",
            font=("Arial", 10, "bold"),
            bg="#2ecc71",
            fg="white",
            relief=tk.FLAT,
            padx=20,
            pady=6,
            command=self._try_connect,
        )
        self._connect_btn.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(
            btn_row,
            text="Cancel",
            font=("Arial", 10),
            relief=tk.FLAT,
            padx=12,
            pady=6,
            command=self._cancel,
        ).pack(side=tk.LEFT)

        self.bind("<Return>", lambda _: self._try_connect())

    def _try_connect(self) -> None:
        key = self._key_var.get().strip()
        if not key:
            self._status_var.set("Please enter an access key.")
            return

        self._status_var.set("Connecting…")
        self._connect_btn.config(state="disabled")
        self.update()

        # Test connection
        import db_handler

        success = db_handler.init(self.SUPABASE_URL, key)

        if success:
            import config as _config

            _config.save_supabase(self.SUPABASE_URL, key)
            self.destroy()
            self._on_success(self.SUPABASE_URL, key)
        else:
            self._status_var.set("Invalid key or connection failed. Please check and try again.")
            self._connect_btn.config(state="normal")

    def _cancel(self) -> None:
        self.destroy()
        self._on_cancel()
