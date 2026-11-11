"""Shared charcoal palette for the native Tk interface."""
from tkinter import ttk


def apply_theme(root):
    root.configure(background="#202226")
    root.option_add("*Font", "{Malgun Gothic} 10")
    for widget in ("Text", "Listbox", "Menu"):
        for key, value in {"background": "#15181d", "foreground": "#eceef1",
                           "selectBackground": "#386cb0", "selectForeground": "#ffffff"}.items():
            root.option_add(f"*{widget}.{key}", value)
    root.option_add("*Text.insertBackground", "#eceef1")
    root.option_add("*Listbox.highlightColor", "#8fc1ff")
    root.option_add("*TCombobox*Listbox.background", "#1d2025")
    root.option_add("*TCombobox*Listbox.foreground", "#eceef1")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background="#202226", foreground="#eceef1", bordercolor="#59616e",
                    lightcolor="#59616e", darkcolor="#202226", focuscolor="#8fc1ff")
    style.configure("TLabel", background="#202226")
    style.configure("Title.TLabel", font=("Malgun Gothic", 18, "bold"))
    style.configure("TButton", background="#363b43", padding=(9, 5))
    style.map("TButton", background=[("active", "#48515e")], foreground=[("disabled", "#929ba8")])
    style.configure("Primary.TButton", background="#386cb0", foreground="#ffffff")
    style.map("Primary.TButton", background=[("active", "#437dc7"), ("disabled", "#30353e")])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground="#1d2025", foreground="#eceef1", insertcolor="#eceef1")
        style.map(name, fieldbackground=[("readonly", "#1d2025"), ("disabled", "#292d33")],
                  foreground=[("readonly", "#eceef1"), ("disabled", "#929ba8")],
                  bordercolor=[("focus", "#8fc1ff")])
    style.configure("Treeview", background="#1d2025", fieldbackground="#1d2025", rowheight=28)
    style.map("Treeview", background=[("selected", "#345d8c")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#353a42", foreground="#eceef1", padding=5)
    style.map("Treeview.Heading", background=[("active", "#48515e")])
    style.configure("TNotebook.Tab", background="#2b2e34", padding=(14, 7))
    style.map("TNotebook.Tab", background=[("selected", "#353b45")], foreground=[("selected", "#ffffff")])
