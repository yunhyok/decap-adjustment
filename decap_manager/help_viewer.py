"""Small, safe renderer for the bundled static help page."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .theme import fit_window


DEFAULT_HELP = Path(__file__).resolve().parent.parent / "assets" / "help.html"
COLORS = {"background": "#202226", "panel": "#2b2e34", "editor": "#15181d",
          "text": "#eceef1", "muted": "#b3bac5", "accent": "#8fc1ff"}


class _HelpParser(HTMLParser):
    """Parse only the formatting needed by the bundled help document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.blocks = "", []
        self._block = None
        self._styles = []
        self._table = None
        self._row = None
        self._cell = None
        self._in_title = False
        self._ignored = False

    def _runs(self):
        return self._cell if self._cell is not None else self._block["runs"] if self._block else None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignored = True
            return
        if self._ignored:
            return
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "p", "li", "pre"):
            self._block = {"type": "heading" if tag.startswith("h") else "item" if tag == "li" else "code" if tag == "pre" else "paragraph",
                           "id": attrs.get("id"), "level": int(tag[1]) if tag.startswith("h") else 0, "runs": []}
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("b", "strong", "code", "a"):
            href = attrs.get("href", "") if tag == "a" else None
            self._styles.append((tag, href if href and href.startswith("#") else None))
        elif tag == "br":
            runs = self._runs()
            if runs is not None:
                runs.append({"text": "\n", "bold": False, "code": False, "href": None})

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignored = False
            return
        if self._ignored:
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("b", "strong", "code", "a"):
            if self._styles:
                self._styles.pop()
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.blocks.append({"type": "table", "rows": self._table})
            self._table = None
        elif tag in ("h1", "h2", "h3", "p", "li", "pre") and self._block:
            if self._block["runs"]:
                self.blocks.append(self._block)
            self._block = None

    def handle_data(self, data):
        if self._ignored:
            return
        if self._in_title:
            self.title += data
            return
        runs = self._runs()
        if runs is None or not data:
            return
        active = [style for style, _ in self._styles]
        link = next((href for style, href in reversed(self._styles) if style == "a"), None)
        runs.append({"text": data, "bold": any(s in active for s in ("b", "strong")),
                     "code": "code" in active or (self._block and self._block["type"] == "code"), "href": link})


def parse_help_html(path=DEFAULT_HELP):
    """Return the safe text model used by the Tk viewer."""
    parser = _HelpParser()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    parser.close()
    return parser.title.strip() or "도움말", parser.blocks


class HtmlHelpViewer(tk.Toplevel):
    def __init__(self, parent, path=DEFAULT_HELP):
        super().__init__(parent)
        self.title("도움말")
        fit_window(self, 900, 700, 520, 400, parent)
        self.configure(bg=COLORS["background"])
        title, blocks = parse_help_html(path)
        self.title(title)
        bar = tk.Frame(self, bg=COLORS["panel"], padx=10, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text="문서 검색", bg=COLORS["panel"], fg=COLORS["text"]).pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query, width=30)
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda _event: self.search())
        ttk.Button(bar, text="찾기", command=self.search).pack(side="left")
        self.status = tk.Label(bar, text="", bg=COLORS["panel"], fg=COLORS["muted"])
        self.status.pack(side="left", padx=8)

        frame = tk.Frame(self, bg=COLORS["background"])
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.text = tk.Text(frame, wrap="word", state="normal", bg=COLORS["editor"], fg=COLORS["text"],
                            insertbackground=COLORS["text"], selectbackground="#386cb0", relief="flat",
                            padx=16, pady=14, font=("Malgun Gothic", 10))
        scroll = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.tag_configure("heading1", font=("Malgun Gothic", 17, "bold"), foreground=COLORS["text"], spacing1=14, spacing3=8)
        self.text.tag_configure("heading2", font=("Malgun Gothic", 13, "bold"), foreground=COLORS["accent"], spacing1=16, spacing3=6)
        self.text.tag_configure("heading3", font=("Malgun Gothic", 11, "bold"), foreground=COLORS["text"], spacing1=10, spacing3=4)
        self.text.tag_configure("bold", font=("Malgun Gothic", 10, "bold"))
        self.text.tag_configure("code", font=("Consolas", 9), background=COLORS["panel"])
        self.text.tag_configure("item", lmargin1=20, lmargin2=34, spacing3=4)
        self.text.tag_configure("search", background="#386cb0", foreground="white")
        for name in ("heading1", "heading2", "heading3"):
            self.text.tag_raise(name)
        self.anchors = {}
        self._render(blocks)
        self.text.configure(state="disabled")
        self.bind("<Escape>", lambda _event: self.destroy())
        for key in "fF":
            self.bind(f"<Control-{key}>", lambda _event: entry.focus_set())

    def _render_runs(self, runs):
        for run in runs:
            tags = [name for name in ("bold", "code") if run[name]]
            if run["href"]:
                tags.append("link_" + run["href"][1:])
                self.text.tag_configure(tags[-1], foreground=COLORS["accent"], underline=True)
                self.text.tag_bind(tags[-1], "<Button-1>", lambda _event, name=run["href"][1:]: self.goto_anchor(name))
            self.text.insert("end", run["text"], tags)

    def _render(self, blocks):
        for block in blocks:
            kind = block["type"]
            if kind == "table":
                widths = max((len(row) for row in block["rows"]), default=1)
                for index, row in enumerate(block["rows"]):
                    self.text.insert("end", "│ ")
                    for cell_index, cell in enumerate(row):
                        self._render_runs(cell)
                        if cell_index < widths - 1:
                            self.text.insert("end", "   │   ")
                    self.text.insert("end", " │\n")
                    if index == 0:
                        self.text.insert("end", "─" * 72 + "\n")
                self.text.insert("end", "\n")
                continue
            if block.get("id"):
                self.anchors[block["id"]] = self.text.index("end-1c")
            if kind == "heading":
                self.text.insert("end", "\n")
                start = self.text.index("end-1c")
            elif kind == "item":
                self.text.insert("end", "• ", "item")
            self._render_runs(block["runs"])
            if kind == "heading":
                self.text.tag_add("heading" + str(block["level"]), start, "end-1c")
            self.text.insert("end", "\n\n" if kind != "item" else "\n", "item" if kind == "item" else ())

    def goto_anchor(self, name):
        if name in self.anchors:
            self.text.yview(self.anchors[name])
            self.text.mark_set("insert", self.anchors[name])

    def search(self):
        query = self.query.get().strip()
        self.text.configure(state="normal")
        self.text.tag_remove("search", "1.0", "end")
        if not query:
            self.status.configure(text="검색어를 입력하세요.")
        else:
            start, count = "1.0", 0
            while True:
                found = self.text.search(query, start, stopindex="end", nocase=True)
                if not found:
                    break
                end = f"{found}+{len(query)}c"
                self.text.tag_add("search", found, end)
                start, count = end, count + 1
            if count:
                self.text.see("search.first")
                self.status.configure(text=f"{count}개 찾음")
            else:
                self.status.configure(text="일치 항목 없음")
        self.text.configure(state="disabled")


def show_help(parent, path=DEFAULT_HELP):
    """Open the help window, or bring the one that is already open to the front."""
    viewer = getattr(parent, "_help_viewer", None)
    if viewer is not None and viewer.winfo_exists():
        viewer.deiconify()
        viewer.lift()
        viewer.focus_set()
        return viewer
    parent._help_viewer = viewer = HtmlHelpViewer(parent, path)
    return viewer
