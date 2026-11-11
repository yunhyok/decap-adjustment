"""An SPD pin layout and synchronized selection of editable decap rows."""

from collections import Counter
import math
import re
import tkinter as tk
from tkinter import font, ttk

from .engine import effective_status, format_capacitance, parse_capacitance


_BG = "#202226"
_PANEL = "#2b2e34"
_INPUT = "#1d2025"
_CANVAS = "#15181d"
_TEXT = "#eceef1"
_MUTED = "#b9c1cc"
_BORDER = "#515966"
_BLUE = "#386cb0"
_FOCUS = "#8fc1ff"
_ACTIVE = "#73c7ad"
_DISABLED = "#a1aab9"
_OTHER = "#e1af71"
_SIDES = ("Top", "Bottom", "Unknown")
_PACKAGE = re.compile(r"(?:^|[_ -])CAP[_ -]+(\d{4}M?)(?=[_ -]|$)", re.I)
_CAPACITY_TERM = re.compile(r"(?<!\S)\d+(?:\.\d+)?(?:\s*[pnum]\d*)?\s*f(?!\S)")
_CTRL = 0x0004
_COLUMNS = ("refdes", "channel", "site", "net", "distance", "size", "capacity", "status", "source", "component")
_LABELS = dict(refdes="RefDes", channel="채널", site="사이트", net="NET", distance="거리 (µm)", size="크기",
               capacity="용량", status="상태", source="출처", component="부품명")
_NATURAL = re.compile(r"(\d+)")


def _point(x, y):
    if all(type(value) in (int, float) and math.isfinite(value) for value in (x, y)):
        return x, y
    return None


def _placement(metadata):
    side = metadata.get("side")
    side = side if side in _SIDES else "Unknown"
    pins = tuple(point for x, y in metadata.get("pins", ()) if (point := _point(x, y)) is not None)
    center = _point(metadata.get("x"), metadata.get("y"))
    if center is None and pins:
        center = tuple(sum(axis) / len(pins) for axis in zip(*pins))
    size, angle = metadata.get("size"), metadata.get("angle")
    body = ()
    if center is not None and isinstance(size, (tuple, list)) and len(size) == 2 and _point(*size) is not None:
        if min(size) > 0 and type(angle) in (int, float) and math.isfinite(angle):
            half_width, half_height = size[0] / 2, size[1] / 2
            theta = math.radians(angle)
            cos, sin = math.cos(theta), math.sin(theta)
            body = tuple((center[0] + x * cos - y * sin, center[1] + x * sin + y * cos)
                         for x, y in ((-half_width, -half_height), (half_width, -half_height),
                                      (half_width, half_height), (-half_width, half_height)))
    return side, center, pins, body


def _capacity_text(value):
    return format_capacitance(value)


def _natural(text):
    """Sort C2 before C10."""
    return [int(part) if part.isdigit() else part.casefold() for part in _NATURAL.split(str(text))]


def _search_text(value):
    return str(value).casefold().replace("μ", "u")


class BoardView(ttk.Frame):
    """Display scoped SPD records and emit explicit status changes by RefDes."""

    def __init__(self, parent, on_apply):
        style = ttk.Style(parent)
        style.configure("DecapBoard.TFrame", background=_BG)
        super().__init__(parent, style="DecapBoard.TFrame", padding=12)
        self.on_apply = on_apply
        self.side = tk.StringVar(self, "Top")
        self.mirror = tk.BooleanVar(self, False)
        self.query = tk.StringVar(self)
        self.counts = tk.StringVar(self)
        self._records, self._locations = {}, {}
        self._values, self._searchable, self._visible = {}, {}, {}
        self._selected = set()
        self._counts = Counter()
        self._items, self._centers = {}, {}
        self._sort = None
        self._reference_pins = 0
        self._scale = self._fit_scale = 1.0
        self._offset = (0, 0)
        self._viewport_size = (0, 0)
        self._fit_pending = True
        self._draw_job = None
        self._select_start = self._pan_start = None
        self._build(style)
        self._refresh()

    def _build(self, style):
        toolbar = tk.Frame(self, background=_PANEL, padx=8, pady=5)
        toolbar.pack(fill="x", pady=(0, 6))
        tk.Label(toolbar, text="면", background=_PANEL, foreground=_TEXT).pack(side="left", padx=(0, 6))
        self.side_buttons = {}
        for side in _SIDES:
            button = tk.Radiobutton(
                toolbar, text=side, variable=self.side, value=side, command=self._change_side,
                indicatoron=False, selectcolor=_BLUE, background=_INPUT, foreground=_TEXT,
                activebackground="#434a55", activeforeground=_TEXT, relief="flat", borderwidth=0,
                padx=8, pady=4, highlightthickness=1, highlightbackground=_PANEL,
                highlightcolor=_FOCUS, takefocus=True,
            )
            button.pack(side="left", padx=(0, 3))
            self.side_buttons[side] = button
        self.mirror_button = tk.Checkbutton(
            toolbar, text="Bottom 좌우 반전 (X)", variable=self.mirror, command=self.fit,
            background=_PANEL, foreground=_TEXT, activebackground=_PANEL, activeforeground=_TEXT,
            disabledforeground=_MUTED, selectcolor=_INPUT, highlightthickness=1,
            highlightbackground=_PANEL, highlightcolor=_FOCUS, takefocus=True,
        )
        self.mirror_button.pack(side="left", padx=6)
        self._button(toolbar, "화면 맞춤", self.fit).pack(side="right")

        # Packed before the panes: a short window shrinks the drawing, never the action bar.
        actions = tk.Frame(self, background=_PANEL, padx=8, pady=5)
        actions.pack(side="bottom", fill="x", pady=(6, 0))
        tk.Label(actions, textvariable=self.counts, background=_PANEL, foreground=_TEXT).pack(side="left")
        self.disable_button = self._button(actions, "선택 비활성화", lambda: self._apply("Disabled"))
        self.disable_button.pack(side="right")
        self.activate_button = self._button(actions, "선택 활성화", lambda: self._apply("Automatic"), primary=True)
        self.activate_button.pack(side="right", padx=(0, 6))

        panes = tk.PanedWindow(
            self, orient="vertical", background=_BG, sashwidth=8, sashrelief="raised", borderwidth=0,
        )
        panes.pack(fill="both", expand=True)
        drawing = tk.Frame(panes, background=_BG)
        table = tk.Frame(panes, background=_PANEL, padx=8, pady=6)
        panes.add(drawing, minsize=120, stretch="always")
        panes.add(table, minsize=135, stretch="never")

        def share():
            # On a short window the list would squeeze the drawing to its minimum; start
            # with at least half for the drawing. Afterwards the user's sash position rules.
            height = panes.winfo_height()
            if height > 1 and panes.sash_coord(0)[1] < height * 0.5:
                panes.sash_place(0, 0, round(height * 0.5))

        def first_layout(_event):
            panes.unbind("<Configure>")
            panes.after_idle(share)

        panes.bind("<Configure>", first_layout)
        self.canvas = tk.Canvas(
            drawing, background=_CANVAS, height=150, width=1, borderwidth=0, takefocus=True,
            highlightthickness=1, highlightbackground=_BORDER, highlightcolor=_FOCUS,
        )
        hint = tk.Frame(drawing, background=_BG)
        hint.pack(side="bottom", fill="x", pady=(3, 0))
        self.canvas.pack(fill="both", expand=True)
        tk.Label(hint, text="클릭·드래그 선택   ·   Ctrl 다중 선택   ·   휠 확대/축소   ·   오른쪽/가운데 드래그 이동   ·   표 더블클릭: 위치로 이동",
                 background=_BG, foreground=_MUTED).pack(side="left")
        for text, color in (("▪ 기준 핀", "#9eaac0"), ("○ 비활성", _DISABLED), ("● 활성", _ACTIVE)):
            tk.Label(hint, text=text, background=_BG, foreground=color).pack(side="right", padx=(6, 0))
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Map>", lambda _event: self._queue_draw())
        self.canvas.bind("<ButtonPress-1>", self._start_selection)
        self.canvas.bind("<B1-Motion>", self._drag_selection)
        self.canvas.bind("<ButtonRelease-1>", self._end_selection)
        for button in (2, 3):
            self.canvas.bind(f"<ButtonPress-{button}>", self._start_pan)
            self.canvas.bind(f"<B{button}-Motion>", self._pan)
            self.canvas.bind(f"<ButtonRelease-{button}>", self._end_pan)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(event, self._wheel)
        self.canvas.bind("<Escape>", self._clear_selection)

        search = tk.Frame(table, background=_PANEL)
        search.pack(fill="x", pady=(0, 6))
        tk.Label(search, text="검색", background=_PANEL, foreground=_TEXT).pack(side="left", padx=(0, 6))
        self.search_entry = tk.Entry(
            search, textvariable=self.query, background=_INPUT, foreground=_TEXT, insertbackground=_TEXT,
            selectbackground=_BLUE, selectforeground=_TEXT, relief="flat", highlightthickness=1,
            highlightbackground=_BORDER, highlightcolor=_FOCUS, takefocus=True,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=3)
        tk.Label(search, text="채널 · 사이트 · NET · RefDes · 크기 · 용량",
                 background=_PANEL, foreground=_MUTED).pack(side="right", padx=(8, 0))
        self.query.trace_add("write", self._refresh)

        self._tree_style(style)
        list_box = tk.Frame(table, background=_PANEL)
        list_box.pack(fill="both", expand=True)
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            list_box, columns=_COLUMNS, show="headings", selectmode="extended",
            height=4, style="DecapBoard.Treeview",
        )
        scale = max(1.0, self.winfo_fpixels("1i") / 96)
        widths = dict(refdes=76, channel=108, site=70, net=150, distance=96, size=70, capacity=79, status=93,
                      source=96, component=185)
        for key in _COLUMNS:
            anchor = "w" if key in ("refdes", "channel", "net", "component") else "e" if key == "distance" else "center"
            self.tree.heading(key, text=_LABELS[key], anchor=anchor, command=lambda column=key: self._sort_by(column))
            width = round(widths[key] * scale)
            self.tree.column(key, width=width, minwidth=width, anchor=anchor, stretch=key in ("net", "component"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        for direction, axis, command, grid in (
            ("vertical", "y", self.tree.yview, dict(row=0, column=1, sticky="ns")),
            ("horizontal", "x", self.tree.xview, dict(row=1, column=0, sticky="ew")),
        ):
            bar = tk.Scrollbar(
                list_box, orient=direction, command=command, background="#46505d",
                activebackground="#626c7a", troughcolor=_INPUT, borderwidth=0, highlightthickness=0,
            )
            bar.grid(**grid)
            self.tree.configure(**{f"{axis}scrollcommand": bar.set})
        self.tree.bind("<<TreeviewSelect>>", self._tree_selected)
        self.tree.bind("<Escape>", self._clear_selection)
        self.tree.bind("<Double-1>", self._locate)
        for key in "aA":
            self.tree.bind(f"<Control-{key}>", self._select_all)


    @staticmethod
    def _button(parent, label, command, primary=False):
        return tk.Button(
            parent, text=label, command=command, background=_BLUE if primary else "#3b414b",
            foreground=_TEXT, disabledforeground=_MUTED, activeforeground=_TEXT,
            activebackground="#437dc7" if primary else "#48515e", relief="flat", borderwidth=0,
            padx=10, pady=4, highlightthickness=1, highlightbackground=_PANEL,
            highlightcolor=_FOCUS, takefocus=True,
        )

    def _tree_style(self, style):
        if "DecapBoard.heading" not in style.element_names():
            style.element_create("DecapBoard.heading", "from", "clam", "border")
            style.element_create("DecapBoard.field", "from", "clam", "field")
        style.layout("DecapBoard.Treeview", [
            ("DecapBoard.field", {"sticky": "nsew", "border": 1, "children": [
                ("Treeview.padding", {"sticky": "nsew", "children": [("Treeview.treearea", {"sticky": "nsew"})]})
            ]})
        ])
        style.layout("DecapBoard.Treeview.Heading", [
            ("DecapBoard.heading", {"sticky": "nsew", "children": [
                ("Treeheading.padding", {"sticky": "nsew", "children": [("Treeheading.text", {"sticky": "ew"})]})
            ]})
        ])
        rowheight = font.nametofont("TkDefaultFont", root=self).metrics("linespace") + 8
        style.configure("DecapBoard.Treeview", background=_INPUT, fieldbackground=_INPUT,
                        foreground=_TEXT, bordercolor=_BORDER, rowheight=rowheight)
        style.map("DecapBoard.Treeview", background=[("selected", _BLUE)],
                  foreground=[("selected", "#ffffff")], bordercolor=[("focus", _FOCUS)])
        style.configure("DecapBoard.Treeview.Heading", background="#383d46", foreground=_TEXT,
                        bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER, padding=(5, 4))

    def set_records(self, records, geometry):
        """Refresh current rows; extra geometry entries are reference-only context."""
        geometry = geometry or {}
        records = {record["refdes"]: dict(record) for record in records}
        locations = {ref: _placement(meta) for ref, meta in geometry.items()}
        locations.update({ref: ("Unknown", None, (), ()) for ref in records.keys() - locations.keys()})
        new_layout = locations != self._locations
        self._records, self._locations = records, locations
        self._values, self._searchable = {}, {}
        for ref, record in records.items():
            metadata = geometry.get(ref, {})
            component = record.get("component", "")
            size = record.get("size")
            if "size" not in record:
                package = _PACKAGE.search(component)
                size = package[1].upper() if package else metadata.get("size")
                if not isinstance(size, str):
                    size = None
            capacitance = record["capacitance_pf"] if "capacitance_pf" in record else parse_capacitance(component)
            if type(capacitance) not in (int, float) or not math.isfinite(capacitance) or capacitance <= 0:
                capacitance = None
            distance = record.get("distance")
            if type(distance) not in (int, float) or not math.isfinite(distance):
                distance = None
            searched = (ref, record.get("channel") or metadata.get("channel"), record.get("site") or metadata.get("site"),
                        record.get("net"), size, _capacity_text(capacitance))
            fields = (*searched[:4], None if distance is None else f"{distance:,.1f}", *searched[4:],
                      effective_status(record), record.get("metadata_source"), component)
            self._values[ref] = tuple(value if value not in ("", None) else "—" for value in fields)
            self._searchable[ref] = (_search_text(" ".join(str(value) for value in searched if value is not None)),
                                     capacitance, distance)
        self._counts = Counter(locations[ref][0] for ref in records)
        self._reference_pins = sum(len(pins) or 1 for ref, (_, center, pins, _) in locations.items()
                                   if ref not in records and center is not None)
        for side, button in self.side_buttons.items():
            button.configure(text=f"{side}  {self._counts[side]}")
        if new_layout:
            self._selected.clear()
            if not self._counts[self.side.get()]:
                self.side.set(next((side for side in _SIDES if self._counts[side]), "Top"))
        self._refresh()
        if new_layout:
            self.fit()

    def _change_side(self):
        self._refresh()
        self.fit()

    def _refresh(self, *_):
        query = _search_text(self.query.get())
        capacity_terms = [parse_capacitance(term) for term in _CAPACITY_TERM.findall(query)]
        terms = _CAPACITY_TERM.sub("", query).split()
        side = self.side.get()
        self.mirror_button.configure(state="normal" if side == "Bottom" else "disabled")
        visible = {
            ref: self._values[ref] for ref, (text, capacity, _) in self._searchable.items()
            if self._locations[ref][0] == side and all(term in text for term in terms)
            and all(term is not None and capacity is not None and math.isclose(term, capacity) for term in capacity_terms)
        }
        if self._sort:
            visible = {ref: visible[ref] for ref in self._sorted(visible)}
        removed = self._visible.keys() - visible.keys()
        if removed:
            self.tree.delete(*removed)
        for ref, values in visible.items():
            if ref not in self._visible:
                self.tree.insert("", "end", iid=ref, values=values)
            elif self._visible[ref] != values:
                self.tree.item(ref, values=values)
        if tuple(visible) != tuple(self._visible):
            self.tree.set_children("", *visible)
        self._visible = visible
        self._select(self._selected)

    def _select(self, refs, reveal=False):
        self._selected = set(refs) & self._visible.keys()
        if self._selected != set(self.tree.selection()):
            self.tree.selection_set(tuple(self._selected))
        if reveal and self._selected:
            first = next(ref for ref in self._visible if ref in self._selected)
            self.tree.focus(first)
            self.tree.see(first)
        counts = Counter(effective_status(self._records[ref]) for ref in self._visible)
        active, disabled = counts["Automatic"], counts["Disabled"]
        other = len(self._visible) - active - disabled
        missing = sum(self._locations[ref][1] is None for ref in self._visible)
        summary = f"표시 {len(self._visible):,}   활성 {active:,}   비활성 {disabled:,}"
        if other:
            summary += f"   기타 상태 {other:,}"
        if missing:
            summary += f"   좌표 없음 {missing:,}"
        self.counts.set(f"{summary}   ·   선택 {len(self._selected):,}")
        selected_statuses = {effective_status(self._records[ref]) for ref in self._selected}
        can_activate = "Disabled" in selected_statuses
        can_disable = "Automatic" in selected_statuses
        self.activate_button.configure(state="normal" if can_activate else "disabled",
                                       background=_BLUE if can_activate else _INPUT)
        self.disable_button.configure(state="normal" if can_disable else "disabled",
                                      background="#3b414b" if can_disable else _INPUT)
        self._queue_draw()

    def _sorted(self, refs):
        """Order rows by the chosen heading; rows without a value stay at the end."""
        column, descending = self._sort
        if column in ("distance", "capacity"):
            index = 2 if column == "distance" else 1
            key = lambda ref: self._searchable[ref][index]
            present = [ref for ref in refs if key(ref) is not None]
        else:
            index = _COLUMNS.index(column)
            key = lambda ref: _natural(self._values[ref][index])
            present = [ref for ref in refs if self._values[ref][index] != "—"]
        missing = [ref for ref in refs if ref not in set(present)]
        return sorted(present, key=key, reverse=descending) + missing

    def _sort_by(self, column):
        """Heading click cycles ascending → descending → original SPD order."""
        if self._sort is None or self._sort[0] != column:
            self._sort = (column, False)
        elif not self._sort[1]:
            self._sort = (column, True)
        else:
            self._sort = None
        for key in _COLUMNS:
            mark = ""
            if self._sort and self._sort[0] == key:
                mark = " ▼" if self._sort[1] else " ▲"
            self.tree.heading(key, text=_LABELS[key] + mark)
        self._refresh()

    def _select_all(self, _event=None):
        self._select(set(self._visible))
        return "break"

    def _locate(self, event):
        """Centre the drawing on the double-clicked row, zooming in when the view is far out."""
        ref = self.tree.identify_row(event.y)
        location = self._locations.get(ref)
        if not ref or location is None or location[1] is None or self._fit_pending:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self._scale = min(self._fit_scale * 256, max(self._scale, self._fit_scale * 6))
        self._offset = (width / 2 - location[1][0] * self._scale * self._x_direction(),
                        height / 2 + location[1][1] * self._scale)
        self._queue_draw()

    def _tree_selected(self, _event):
        refs = set(self.tree.selection())
        if refs != self._selected:
            self._select(refs)

    def _clear_selection(self, _event):
        self._select_start = None
        self.canvas.delete("marquee")
        self._select(set())
        return "break"

    def _apply(self, status):
        previous = "Disabled" if status == "Automatic" else "Automatic"
        refs = {ref for ref in self._selected & self._visible.keys()
                if effective_status(self._records[ref]) == previous}
        if refs:
            self.on_apply(refs, status)

    def _x_direction(self):
        return -1 if self.side.get() == "Bottom" and self.mirror.get() else 1

    def _placements_on_screen(self):
        """Visible decaps of the chosen side, plus the reference pads as context on every side."""
        side = self.side.get()
        return ((ref, center, pins, body) for ref, (face, center, pins, body) in self._locations.items()
                if center is not None and (ref not in self._records or (face == side and ref in self._visible)))

    def fit(self):
        self._fit_pending = True
        self._queue_draw()

    def _fit(self, width, height):
        points = [point for _, center, pins, body in self._placements_on_screen() for point in (center, *pins, *body)]
        if not points:
            self._offset = (width / 2, height / 2)
            self._scale = self._fit_scale = 1.0
            return
        xs, ys = zip(*points)
        left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
        self._scale = self._fit_scale = min(
            max(width - 56, 1) / max(right - left, 1),
            max(height - 56, 1) / max(top - bottom, 1),
        )
        self._offset = (
            width / 2 - (left + right) / 2 * self._scale * self._x_direction(),
            height / 2 + (bottom + top) / 2 * self._scale,
        )

    def _resize(self, event):
        width, height = self._viewport_size
        self._viewport_size = (event.width, event.height)
        if width and height and not self._fit_pending:
            dx, dy = self._offset
            self._offset = (dx + (event.width - width) / 2, dy + (event.height - height) / 2)
        self._queue_draw()

    def _queue_draw(self):
        if self._draw_job is None:
            self._draw_job = self.after_idle(self._draw)

    def destroy(self):
        if self._draw_job is not None:
            self.after_cancel(self._draw_job)
        super().destroy()

    def _draw(self):
        self._draw_job = None
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if not canvas.winfo_ismapped() or width <= 1 or height <= 1:
            return
        if self._fit_pending:
            self._fit(width, height)
            self._fit_pending = False
        canvas.delete("board")
        self._items, self._centers = {}, {}
        dx, dy = self._offset
        scale = self._scale
        xscale = scale * self._x_direction()
        placements = list(self._placements_on_screen())
        # Reference pads sit behind the selectable decaps; the selection is drawn last, on top.
        placements.sort(key=lambda placement: (placement[0] in self._records, placement[0] in self._selected))
        # Few or magnified reference pads are drawn larger so a small reference stays visible.
        pad = 2 if self._reference_pins <= 600 or scale >= self._fit_scale * 4 else 1
        decaps = 0
        for ref, center, pins, body in placements:
            x, y = center[0] * xscale + dx, dy - center[1] * scale
            projected = tuple((px * xscale + dx, dy - py * scale) for px, py in pins)
            if ref not in self._records:
                for px, py in projected or ((x, y),):
                    if -2 <= px <= width + 2 and -2 <= py <= height + 2:
                        canvas.create_rectangle(px - pad, py - pad, px + pad, py + pad, fill="#7c899f",
                                                width=0, tags="board")
                continue
            decaps += 1
            outline = tuple((px * xscale + dx, dy - py * scale) for px, py in body)
            xs, ys = zip((x, y), *projected, *outline)
            if max(xs) < -16 or min(xs) > width + 16 or max(ys) < -16 or min(ys) > height + 16:
                continue
            self._draw_decap(ref, x, y, projected, outline)
        if not decaps:
            message = ("SPD를 불러오고 기준 부품과 대상 디캡을 지정하세요." if not self._records else
                       "표시할 좌표가 없습니다. 아래 목록에서 RefDes를 확인하세요." if self._visible else
                       "이 면과 검색 조건에 맞는 디캡이 없습니다.")
            canvas.create_text(width / 2, height / 2, text=message, fill=_MUTED,
                               width=max(width - 30, 20), justify="center", tags="board")
        canvas.tag_raise("marquee")

    def _draw_decap(self, ref, x, y, pins, body):
        canvas = self.canvas
        status = effective_status(self._records[ref])
        color = {"Automatic": _ACTIVE, "Disabled": _DISABLED}.get(status, _OTHER)
        selected = ref in self._selected
        outline = _FOCUS if selected else color
        radius = 6 if selected else 4
        items = []
        if body and math.dist(body[0], body[2]) >= 9:
            items.append(canvas.create_polygon(body, fill=_CANVAS if status == "Disabled" else color,
                                               outline=outline, width=2 if selected else 1, tags="board"))
        # A pin-aligned symbol keeps footprints with no known dimensions visible.
        elif len(pins) == 2 and (length := math.dist(*pins)) >= 9:
            thickness = min(12, max(4, length / 3))
            items.append(canvas.create_line(*pins[0], *pins[1], fill=outline,
                                            width=thickness + 4 if selected else thickness, tags="board"))
            if selected or status == "Disabled":
                items.append(canvas.create_line(*pins[0], *pins[1], fill=_CANVAS if status == "Disabled" else color,
                                                width=max(thickness - 2, 2), tags="board"))
        else:
            items.append(canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius, fill=_CANVAS if status == "Disabled" else color,
                outline=outline, width=2 if selected else 1, tags="board",
            ))
        for px, py in pins:
            if abs(px - x) > 5 or abs(py - y) > 5:
                items.append(canvas.create_rectangle(px - 2, py - 2, px + 2, py + 2, fill=color,
                                                     outline=outline, tags="board"))
        if selected and len(self._selected) == 1:
            canvas.create_text(x + 9, y - 9, text=ref, anchor="sw", fill=_FOCUS, tags="board")
        self._items.update((item, ref) for item in items)
        self._centers[ref] = (x, y)

    def _start_selection(self, event):
        self.canvas.focus_set()
        self._select_start = (event.x, event.y, bool(event.state & _CTRL))
        self.canvas.delete("marquee")

    def _drag_selection(self, event):
        if self._select_start is not None:
            x, y, _ = self._select_start
            self.canvas.delete("marquee")
            self.canvas.create_rectangle(x, y, event.x, event.y, outline=_FOCUS,
                                         dash=(4, 3), width=2, tags="marquee")

    def _end_selection(self, event):
        if self._select_start is None:
            return
        x, y, extend = self._select_start
        self._select_start = None
        self.canvas.delete("marquee")
        if max(abs(event.x - x), abs(event.y - y)) < 4:
            hits = self._hit_refs(x - 4, y - 4, x + 4, y + 4)
            if hits:
                nearest = min(hits, key=lambda ref: (math.dist((x, y), self._centers[ref]), ref))
                self._select(self._selected ^ {nearest} if extend else {nearest}, reveal=True)
            elif not extend:
                self._select(set())
            return
        hits = self._hit_refs(min(x, event.x), min(y, event.y), max(x, event.x), max(y, event.y))
        self._select(self._selected | hits if extend else hits, reveal=True)

    def _hit_refs(self, *bounds):
        return {self._items[item] for item in self.canvas.find_overlapping(*bounds) if item in self._items}

    def _start_pan(self, event):
        self._pan_start = (event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _pan(self, event):
        if self._pan_start is not None:
            x, y = self._pan_start
            dx, dy = self._offset
            self._offset = (dx + event.x - x, dy + event.y - y)
            self._pan_start = (event.x, event.y)
            self._queue_draw()

    def _end_pan(self, _event):
        self._pan_start = None
        self.canvas.configure(cursor="")

    def _wheel(self, event):
        if event.num in (4, 5):
            steps = 1 if event.num == 4 else -1
        else:
            steps = max(-4, min(4, event.delta / 120))
        new_scale = min(self._fit_scale * 256, max(self._fit_scale / 32, self._scale * 1.2 ** steps))
        ratio = new_scale / self._scale
        dx, dy = self._offset
        self._offset = (event.x + (dx - event.x) * ratio, event.y + (dy - event.y) * ratio)
        self._scale = new_scale
        self._queue_draw()
        return "break"
