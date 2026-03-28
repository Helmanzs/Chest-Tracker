"""
main.py
-------
Entry point.  Just boots Tk and hands off to App.
"""

import tkinter as tk
from app import App


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
