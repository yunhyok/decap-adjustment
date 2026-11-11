"""Themed modal text prompt used instead of the unthemed tkinter.simpledialog boxes."""
import tkinter as tk
from tkinter import ttk

from .theme import center_window, px


class TextPrompt(tk.Toplevel):
    """Ask for one value (or several lines); ``convert`` errors stay inside the dialog."""

    def __init__(self, parent, title, message, initial="", *, multiline=False, convert=None):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(multiline, multiline)
        self.result = None
        self._convert = convert or (lambda text: text)
        panel = ttk.Frame(self, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=message, justify="left", wraplength=px(self, 460)).pack(anchor="w")
        if multiline:
            self.field = tk.Text(panel, width=52, height=8, undo=True)
            self.field.insert("1.0", initial)
            self.field.pack(fill="both", expand=True, pady=(10, 0))
        else:
            self.field = ttk.Entry(panel, width=44)
            self.field.insert(0, initial)
            self.field.selection_range(0, "end")
            self.field.pack(fill="x", pady=(10, 0))
            self.field.bind("<Return>", lambda _event: self.accept())
        self.error = tk.StringVar()
        ttk.Label(panel, textvariable=self.error, foreground="#f0a8a0", justify="left",
                  wraplength=px(self, 460)).pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="확인", command=self.accept, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_window(self, parent)
        self.deiconify()
        self.field.focus_set()
        self.grab_set()
        self.wait_window()

    def text(self):
        if isinstance(self.field, tk.Text):
            return self.field.get("1.0", "end-1c")
        return self.field.get()

    def accept(self):
        try:
            self.result = self._convert(self.text())
        except ValueError as exc:
            self.error.set(str(exc))
            return
        self.destroy()


def ask_text(parent, title, message, initial="", *, multiline=False, convert=None):
    """Return the converted input, or None when the prompt was cancelled."""
    return TextPrompt(parent, title, message, initial, multiline=multiline, convert=convert).result
