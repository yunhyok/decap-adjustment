"""Shared charcoal palette, DPI scaling and window placement for the native Tk interface."""
from tkinter import font, ttk


FONT_FAMILY = "Malgun Gothic"


def ui_scale(widget):
    """Screen pixels per design pixel: 1.0 at 96 dpi (Windows 100 %), 1.5 at 150 %."""
    return max(1.0, widget.winfo_fpixels("1i") / 96)


def px(widget, value):
    """Convert a 96-dpi design length to screen pixels."""
    return round(value * ui_scale(widget))


def fit_window(window, width, height, min_width, min_height, parent=None):
    """Size a window in design pixels, keep it on the screen and centre it.

    Fonts grow with the display scale but Tk geometry is in raw pixels, so a fixed
    size clips the lower controls on scaled or small displays.
    """
    scale = ui_scale(window)
    screen_width, screen_height = window.winfo_screenwidth(), window.winfo_screenheight()
    # Leave room for the window frame, title bar and taskbar.
    usable_width = max(320, screen_width - round(24 * scale))
    usable_height = max(240, screen_height - round(96 * scale))
    width, height = min(round(width * scale), usable_width), min(round(height * scale), usable_height)
    window.minsize(min(round(min_width * scale), width), min(round(min_height * scale), height))
    if parent is not None and parent.winfo_viewable():
        x, y = _over_parent(window, parent, width, height)
    else:
        x, y = max(0, (screen_width - width) // 2), max(0, (usable_height - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _over_parent(window, parent, width, height):
    """Centre on the parent, kept inside the whole desktop (the parent may be on another monitor)."""
    left, top = window.winfo_vrootx(), window.winfo_vrooty()
    right, bottom = left + window.winfo_vrootwidth(), top + window.winfo_vrootheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))


def center_window(window, parent):
    """Place a content-sized dialog over its parent instead of the screen corner."""
    window.update_idletasks()
    if not parent.winfo_viewable():
        return
    x, y = _over_parent(window, parent, window.winfo_reqwidth(), window.winfo_reqheight())
    window.geometry(f"+{x}+{y}")


def apply_theme(root):
    root.configure(background="#202226")
    root.option_add("*Font", f"{{{FONT_FAMILY}}} 10")
    for widget in ("Text", "Listbox", "Menu"):
        for key, value in {"background": "#15181d", "foreground": "#eceef1",
                           "selectBackground": "#386cb0", "selectForeground": "#ffffff"}.items():
            root.option_add(f"*{widget}.{key}", value)
    root.option_add("*Text.insertBackground", "#eceef1")
    root.option_add("*Listbox.highlightColor", "#8fc1ff")
    root.option_add("*TCombobox*Listbox.background", "#1d2025")
    root.option_add("*TCombobox*Listbox.foreground", "#eceef1")
    root.option_add("*Toplevel.background", "#202226")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background="#202226", foreground="#eceef1", bordercolor="#59616e",
                    lightcolor="#59616e", darkcolor="#202226", focuscolor="#8fc1ff")
    style.configure("TLabel", background="#202226")
    style.configure("Muted.TLabel", foreground="#b9c1cc")
    style.configure("TButton", background="#363b43", padding=(9, 5))
    style.map("TButton", background=[("active", "#48515e")], foreground=[("disabled", "#929ba8")])
    style.configure("Primary.TButton", background="#386cb0", foreground="#ffffff")
    style.map("Primary.TButton", background=[("active", "#437dc7"), ("disabled", "#30353e")])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground="#1d2025", foreground="#eceef1", insertcolor="#eceef1",
                        background="#363b43", arrowcolor="#eceef1")
        style.map(name, fieldbackground=[("readonly", "#1d2025"), ("disabled", "#292d33")],
                  foreground=[("readonly", "#eceef1"), ("disabled", "#929ba8")],
                  background=[("active", "#48515e"), ("disabled", "#292d33")],
                  arrowcolor=[("disabled", "#929ba8")], bordercolor=[("focus", "#8fc1ff")])
    # clam keeps light hover colours unless every state is mapped to the charcoal palette.
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background="#202226", foreground="#eceef1", indicatorbackground="#1d2025",
                        indicatorforeground="#8fc1ff")
        style.map(name, background=[("active", "#2b2e34")], foreground=[("disabled", "#929ba8")],
                  indicatorbackground=[("pressed", "#363b43"), ("disabled", "#292d33")])
    style.configure("TLabelframe", background="#202226", bordercolor="#59616e")
    style.configure("TLabelframe.Label", background="#202226", foreground="#eceef1")
    row_height = font.Font(root=root, family=FONT_FAMILY, size=10).metrics("linespace") + px(root, 10)
    style.configure("Treeview", background="#1d2025", fieldbackground="#1d2025", rowheight=max(px(root, 28), row_height))
    style.map("Treeview", background=[("selected", "#345d8c")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#353a42", foreground="#eceef1", padding=5)
    style.map("Treeview.Heading", background=[("active", "#48515e")])
    style.configure("TNotebook", background="#202226", bordercolor="#59616e")
    style.configure("TNotebook.Tab", background="#2b2e34", padding=(14, 7))
    style.map("TNotebook.Tab", background=[("selected", "#353b45"), ("active", "#31363e")],
              foreground=[("selected", "#ffffff")])
    for orientation in ("Vertical", "Horizontal"):
        style.configure(f"{orientation}.TScrollbar", background="#46505d", troughcolor="#1d2025",
                        bordercolor="#202226", lightcolor="#46505d", darkcolor="#46505d",
                        arrowcolor="#b9c1cc", gripcount=0, arrowsize=px(root, 13))
        style.map(f"{orientation}.TScrollbar", background=[("disabled", "#2b2e34"), ("active", "#626c7a")],
                  lightcolor=[("disabled", "#2b2e34"), ("active", "#626c7a")],
                  darkcolor=[("disabled", "#2b2e34"), ("active", "#626c7a")],
                  arrowcolor=[("disabled", "#515966")])
    style.configure("Horizontal.TProgressbar", background="#386cb0", troughcolor="#1d2025",
                    bordercolor="#59616e", lightcolor="#386cb0", darkcolor="#386cb0")
