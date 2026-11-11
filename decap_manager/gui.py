"""Korean desktop workflow: open an SPD, queue commands, preview, apply and save."""
from __future__ import annotations

import json
from copy import deepcopy
import math
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from . import __version__
from .dialogs import ask_text
from .engine import (ADJUSTABLE_STATUSES, compute, editable_capacitance, effective_status,
                     format_capacitance, parse_capacitance_input, parse_expression)
from .excel_io import fingerprint, inspect_workbook, read_commands
from .shortage import find_shortages, shortage_choices
from .spd_dialog import open_spd, run_spd_task, SpdScopeDialog
from .spd import WRITABLE_STATUSES, read_spd, save_spd, validate_component_metadata
from .board_view import BoardView
from .help_viewer import show_help
from .theme import FONT_FAMILY, apply_theme, center_window, fit_window, px


LEVEL_LABELS = {"error": "오류", "warning": "경고", "info": "정보"}
SOURCE_LABELS = {"Manual": "수동 지정", "SPICE comment": "SPICE 주석"}


def asset_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def format_distance(value):
    """Distances are µm floats derived from unit conversion; show one decimal, not float noise."""
    if type(value) in (int, float) and math.isfinite(value):
        return f"{value:,.1f}"
    return ""


def unused_name(directory, stem, suffix):
    """First of stem, stem_2, stem_3 … that does not exist yet in the directory."""
    candidate, index = f"{stem}{suffix}", 1
    while os.path.lexists(Path(directory) / candidate) and index < 1000:
        index += 1
        candidate = f"{stem}_{index}{suffix}"
    return candidate


def table(parent, columns, height=12, *, grid=None, xscroll=True):
    """A scrollable tree. It is packed to fill the parent unless ``grid`` gives grid options.

    Lists with a few short columns pass ``xscroll=False`` to give that height to their rows.
    """
    frame = ttk.Frame(parent)
    if grid is None:
        frame.pack(fill="both", expand=True, pady=4)
    else:
        frame.grid(sticky="nsew", pady=4, **grid)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
    for key, label, width in columns:
        tree.heading(key, text=label)
        tree.column(key, width=px(parent, width), minwidth=px(parent, 65), stretch=True)
    y = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=y.set)
    tree.grid(row=0, column=0, sticky="nsew")
    y.grid(row=0, column=1, sticky="ns")
    if xscroll:
        x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=x.set)
        x.grid(row=1, column=0, sticky="ew")
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    return tree


class QuantityShortageDialog(tk.Toplevel):
    def __init__(self, parent, shortage, choices):
        super().__init__(parent)
        self.title("자리 부족 · 수량 조정 선택")
        fit_window(self, 1000, 700, 850, 620, parent)
        self.transient(parent)
        self.result = None
        self.shortage, self.choices = shortage, choices
        panel = ttk.Frame(self, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        # Only the two lists carry weight, so a short screen shrinks them and never the buttons.
        panel.rowconfigure(2, weight=1)
        panel.rowconfigure(4, weight=1)
        ttk.Label(panel, text=shortage["net"], font=(FONT_FAMILY, 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(panel, text=f"조정 가능한 자리 {shortage['total']}개 · 현재 활성 {shortage['active']}개 · "
                             f"명령 적용 시 {shortage['requested_active']}개 → {shortage['deficit']}개 초과").grid(row=1, column=0, sticky="w", pady=8)
        self.amounts = table(panel, [("component", "부품", 290), ("total", "현재 자리", 100),
                                     ("active", "현재 활성", 100), ("requested", "원래 명령 적용", 130),
                                     ("adjusted", "선택안 적용", 130)], 4, grid=dict(row=2, column=0))
        for row in shortage["components"]:
            self.amounts.insert("", "end", values=(row["component"], row["total"], row["active"], row["requested_active"], "—"))
        ttk.Label(panel, text="조정안을 선택하세요. 다른 용량을 줄이는 안은 가까운 Automatic 자리부터 감소합니다.").grid(row=3, column=0, sticky="w", pady=(12, 4))
        self.choices_table = table(panel, [("label", "조정안", 850)], 4, grid=dict(row=4, column=0))
        self.choices_table.configure(selectmode="browse")
        for index, choice in enumerate(choices):
            self.choices_table.insert("", "end", iid=str(index), values=(choice["label"],))
        self.choices_table.bind("<<TreeviewSelect>>", self.select_choice)
        self.detail = tk.StringVar(value="선택한 NET의 계산을 통과한 대안만 표시합니다." if choices else
                                 "자동으로 제안할 수 있는 조정안이 없습니다. 명령과 변환 규칙을 직접 확인하세요.")
        ttk.Label(panel, textvariable=self.detail, wraplength=px(self, 800), justify="left").grid(row=5, column=0, sticky="ew", pady=10)
        ttk.Label(panel, text="명령 목록은 변경하지 않습니다. 선택은 이번 계산에만 적용되며 다시 계산하면 다시 선택합니다.\n"
                              "필요한 연쇄 규칙을 확인하고 전체 NET·재배치 검증을 통과해야 결과를 적용할 수 있습니다.",
                  wraplength=px(self, 800), justify="left").grid(row=6, column=0, sticky="ew", pady=6)
        buttons = ttk.Frame(panel)
        buttons.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        self.apply_button = ttk.Button(buttons, text="선택안으로 다시 계산", command=self.accept, state="disabled")
        self.apply_button.pack(side="right")
        ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda _event: self.accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.wait_window()

    def select_choice(self, _event=None):
        selected = self.choices_table.selection()
        if not selected:
            self.apply_button.configure(state="disabled")
            return
        choice = self.choices[int(selected[0])]
        self.detail.set(choice["description"])
        after = {row["component"]: row["after_active"] for row in choice["summary"]}
        for item in self.amounts.get_children():
            values = list(self.amounts.item(item, "values"))
            values[-1] = after.get(values[0], 0)
            self.amounts.item(item, values=values)
        self.apply_button.configure(state="normal")

    def accept(self):
        selected = self.choices_table.selection()
        if selected:
            self.result = self.choices[int(selected[0])]
            self.destroy()


class MappingDialog(tk.Toplevel):
    FIELDS = {
        "commands": [("net", "NET (없으면 Channel + SITE)"), ("channel", "Channel"), ("site", "SITE"),
                     ("component", "Component (세로 표)"), ("expression", "명령 (세로 표)")],
    }

    def __init__(self, parent, path, kind, initial=None):
        super().__init__(parent)
        self.title("Excel 시트 · 헤더 · 열 확인")
        fit_window(self, 1040, 760, 760, 680, parent)
        self.transient(parent)
        self.result = None
        self.path = path
        self.book = load_workbook(path, read_only=True, data_only=False)
        self.kind = kind
        detection_error = ""
        try:
            self.candidates = inspect_workbook(path, kind)
        except ValueError as exc:
            detection_error = str(exc)
            self.candidates = []
        selected = initial or (self.candidates[0] if self.candidates else {
            "sheet": self.book.sheetnames[0], "header_row": 1, "columns": {}})
        main = ttk.Frame(self, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        # Rows are placed by number; only the cell preview (and, less, the wide list) may shrink,
        # so a short screen never pushes the confirm buttons out of the dialog.
        ttk.Label(main, text=Path(path).name, font=(FONT_FAMILY, 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="자동 감지 결과를 확인하세요. 열은 A, B 또는 1, 2 형식으로 수정할 수 있습니다.").grid(row=1, column=0, sticky="w", pady=6)
        if detection_error:
            ttk.Label(main, text=detection_error, wraplength=px(self, 780)).grid(row=2, column=0, sticky="w")
        if len(self.candidates) > 1:
            self.candidate_choice = ttk.Combobox(main, state="readonly", values=[
                f"{m['sheet']} · 헤더 {m['header_row']}행" for m in self.candidates])
            self.candidate_choice.grid(row=3, column=0, sticky="ew", pady=4)
            self.candidate_choice.current(0)
            self.candidate_choice.bind("<<ComboboxSelected>>", self.apply_candidate)
        top = ttk.Frame(main)
        top.grid(row=4, column=0, sticky="ew")
        self.sheet = tk.StringVar(value=selected["sheet"])
        self.header = tk.IntVar(value=selected["header_row"])
        ttk.Label(top, text="시트").pack(side="left")
        ttk.Combobox(top, textvariable=self.sheet, values=self.book.sheetnames, state="readonly", width=25).pack(side="left", padx=8)
        ttk.Label(top, text="헤더 행").pack(side="left")
        ttk.Spinbox(top, textvariable=self.header, from_=1, to=1048576, width=7).pack(side="left", padx=8)
        ttk.Button(top, text="행 미리보기", command=self.refresh).pack(side="left")
        ttk.Label(main, text="셀 미리보기 · 열 경계를 끌어 너비 조절 / 가로 스크롤로 오른쪽 열 확인").grid(row=5, column=0, sticky="w", pady=(6, 0))
        main.rowconfigure(6, weight=3)
        self.samples = table(main, [], 5, grid=dict(row=6, column=0))
        self.samples.tag_configure("header", background="#353a42", foreground="#eceef1", font=(FONT_FAMILY, 10, "bold"))
        self.samples.bind("<ButtonRelease-1>", self.show_preview_cell)
        detail = ttk.Frame(main)
        detail.grid(row=7, column=0, sticky="ew")
        self.cell_address = tk.StringVar(value="셀 선택")
        self.cell_value = tk.StringVar()
        ttk.Label(detail, textvariable=self.cell_address, width=9).pack(side="left")
        ttk.Entry(detail, textvariable=self.cell_value, state="readonly").pack(side="left", fill="x", expand=True)
        form = ttk.Frame(main)
        form.grid(row=8, column=0, sticky="ew", pady=8)
        self.fields = {}
        for i, (key, label) in enumerate(self.FIELDS[kind]):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            value = selected.get("columns", {}).get(key)
            var = tk.StringVar(value=get_column_letter(value) if value else "")
            self.fields[key] = var
            ttk.Entry(form, textvariable=var, width=12).grid(row=i, column=1, sticky="w", padx=12)
        self.template = tk.StringVar(value=selected.get("net_template", "U{site}_{channel}"))
        if kind == "commands":
            ttk.Label(form, text="NET 조합 형식").grid(row=5, column=0, sticky="w")
            ttk.Entry(form, textvariable=self.template, width=35).grid(row=5, column=1, sticky="w", padx=12)
            ttk.Label(main, text="가로 명령표: 부품명 = 열 (한 줄에 하나). 세로 표에서는 비워 두세요.").grid(row=9, column=0, sticky="w")
            main.rowconfigure(10, weight=1)
            self.wide = tk.Text(main, height=5, font=(FONT_FAMILY, 10))
            self.wide.grid(row=10, column=0, sticky="nsew")
            for comp, col in selected.get("component_columns", {}).items():
                self.wide.insert("end", f"{comp} = {get_column_letter(col)}\n")
        buttons = ttk.Frame(main)
        buttons.grid(row=11, column=0, sticky="ew", pady=10)
        ttk.Button(buttons, text="이 설정 사용", command=self.accept, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh()
        self.grab_set()
        self.wait_window()
        self.book.close()

    def apply_candidate(self, _event=None):
        mapping = self.candidates[self.candidate_choice.current()]
        self.sheet.set(mapping["sheet"])
        self.header.set(mapping["header_row"])
        for key, var in self.fields.items():
            col = mapping["columns"].get(key)
            var.set(get_column_letter(col) if col else "")
        if self.kind == "commands":
            self.wide.delete("1.0", "end")
            for component, col in mapping.get("component_columns", {}).items():
                self.wide.insert("end", f"{component} = {get_column_letter(col)}\n")
        self.refresh()

    def header_row(self):
        try:
            row = self.header.get()
        except tk.TclError:
            row = 0
        if row < 1:
            raise ValueError("헤더 행에는 1 이상의 정수를 입력하세요.")
        return row

    def refresh(self):
        try:
            self.samples.delete(*self.samples.get_children())
            self.cell_address.set("셀 선택")
            self.cell_value.set("")
            ws = self.book[self.sheet.get()]
            if ws.max_row is None or ws.max_column is None:
                ws.calculate_dimension(force=True)
            header = self.header_row()
            start = max(1, header - 2)
            rows = [["" if value is None else str(value) for value in row] for row in ws.iter_rows(
                min_row=start, max_row=min(start + 6, ws.max_row), max_col=ws.max_column, values_only=True)]
            columns = [get_column_letter(i) for i in range(1, ws.max_column + 1)]
            self.samples.configure(columns=["row", *columns])
            self.samples.heading("row", text="행")
            self.samples.column("row", width=px(self, 52), minwidth=px(self, 48), stretch=False, anchor="center")
            cell_font = font.Font(font=ttk.Style(self).lookup("Treeview", "font") or "TkDefaultFont")
            for index, column in enumerate(columns):
                width = max(cell_font.measure(line) for row in rows for line in (row[index].splitlines() or [""])) if rows else 0
                self.samples.heading(column, text=column)
                self.samples.column(column, width=min(px(self, 360), max(px(self, 90), width + px(self, 28))),
                                    minwidth=px(self, 60), stretch=False)
            for row_number, values in enumerate(rows, start):
                self.samples.insert("", "end", values=[row_number, *values],
                                    tags=("header",) if row_number == header else ())
            self.samples.xview_moveto(0)
        except Exception as exc:
            messagebox.showerror("헤더 확인", str(exc), parent=self)

    def show_preview_cell(self, event):
        item = self.samples.identify_row(event.y)
        column = self.samples.identify_column(event.x)
        if not item or not column:
            return
        index = int(column[1:]) - 1
        values = self.samples.item(item, "values")
        if not 1 <= index < len(values):
            return
        self.cell_address.set(f"{get_column_letter(index)}{values[0]}")
        self.cell_value.set(values[index])

    def accept(self):
        try:
            def col(value):
                value = value.strip().upper()
                try:
                    number = int(value) if value.isdigit() else column_index_from_string(value)
                except ValueError:
                    number = 0
                if not 1 <= number <= 16384:
                    raise ValueError(f"열 지정 {value!r}을(를) 확인하세요. A, B 또는 1, 2 형식을 사용합니다.")
                return number
            mapping = {"sheet": self.sheet.get(), "header_row": self.header_row(),
                       "columns": {k: col(v.get()) for k, v in self.fields.items() if v.get().strip()}}
            if self.kind == "commands":
                wide = {}
                for line in self.wide.get("1.0", "end").splitlines():
                    if line.strip():
                        if "=" not in line:
                            raise ValueError(f"가로 명령표 {line.strip()!r}: '부품명 = 열' 형식으로 입력하세요.")
                        name, value = line.rsplit("=", 1)
                        if name.strip() in wide:
                            raise ValueError("부품명 중복 매핑입니다.")
                        wide[name.strip()] = col(value)
                mapping.update(component_columns=wide, net_template=self.template.get().strip())
            # The readers validate required fields and data before this mapping is committed.
            read_commands(self.path, mapping)
            self.result = mapping
            self.destroy()
        except Exception as exc:
            messagebox.showerror("열 설정 확인", str(exc), parent=self)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Decap Adjustment {__version__}")
        apply_theme(self)
        fit_window(self, 1220, 900, 900, 680)
        icon = asset_root() / "assets" / "app.ico"
        if icon.exists():
            try:
                # -default also applies the icon to every dialog; .ico bitmaps are Windows-only.
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        self.paths, self.hashes = {}, {}
        self.spd_layout = self.spd_selection = None
        self.records, self.manual, self.aliases, self.policies = [], [], {}, []
        self._pristine_manual = []
        self.capacities, self.board_geometry = {}, {}
        self.metadata_overrides = {}
        self.result = None
        self._tracking = False
        self._undo, self._redo = [], []
        self._last_state = None
        self._saved_state = None
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        self.build_files(container)
        # Packed before the expanding tabs: a short or scaled window must never hide Save.
        footer = ttk.Frame(container)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.notice = tk.StringVar(value="SPD 파일을 선택하세요.")
        self.save_button = ttk.Button(footer, text="SPD 다른 이름으로 저장…", command=self.save, state="disabled", style="Primary.TButton")
        self.save_button.pack(side="right", padx=(12, 0))
        notice = ttk.Label(footer, textvariable=self.notice, justify="left")
        notice.pack(side="left", fill="x", expand=True)
        notice.bind("<Configure>", lambda event: notice.configure(wraplength=max(event.width - 4, 100)))
        self.tabs = ttk.Notebook(container)
        self.tabs.pack(fill="both", expand=True, pady=(8, 0))
        commands_page, board_page = (ttk.Frame(self.tabs, padding=8) for _ in range(2))
        self.tabs.add(commands_page, text="1  거리 기준 · 수량 조절")
        self.tabs.add(board_page, text="2  도면 · 직접 선택")
        self.command_tabs = ttk.Notebook(commands_page)
        self.command_tabs.pack(fill="both", expand=True)
        self.pages = [None]
        for title in ("명령 목록", "배치 · 변환", "계산 미리보기"):
            page = ttk.Frame(self.command_tabs, padding=8)
            self.command_tabs.add(page, text=title)
            self.pages.append(page)
        self.build_commands()
        self.build_layout()
        self.build_preview()
        self.board = BoardView(board_page, self.apply_selection)
        self.board.pack(fill="both", expand=True)
        self.build_menu()
        self.protocol("WM_DELETE_WINDOW", self.close)
        # Caps Lock reports the upper-case keysym; Ctrl+Shift+Z is the usual redo alternative.
        for keys, action in (("zZ", self.undo), ("yY", self.redo), ("sS", lambda event: self.save()),
                             ("oO", lambda event: self.choose_spd())):
            for key in keys:
                self.bind(f"<Control-{key}>", action)
        for key in "zZ":
            self.bind(f"<Control-Shift-{key}>", self.redo)
        self.bind("<F1>", lambda event: self.help())
        self._tracking = True
        self._last_state = self.snapshot()
        self._saved_state = deepcopy(self._last_state)
        self.update_history_menu()

    def part_metadata(self, component):
        base = self.spd_layout.component_metadata.get(component, {}) if self.spd_layout else {}
        return self.metadata_overrides.get(component, base)

    def capacity_label(self, component):
        return format_capacitance(self.capacities.get(component)) or "미인식 · 수동 순서"

    def edit_component_metadata(self):
        if not self.spd_layout:
            return
        dialog = tk.Toplevel(self)
        dialog.title("부품 정보 · 모델 주석 / 수동 지정")
        fit_window(dialog, 840, 550, 700, 440, self)
        dialog.transient(self)
        panel = ttk.Frame(dialog, padding=12)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="SPICE 주석의 공칭 용량·사이즈를 사용합니다. 수동 값은 SPD 주석으로 저장하며 회로 모델 값은 바꾸지 않습니다.",
                  wraplength=px(self, 780), justify="left").pack(anchor="w")
        # The buttons and form are packed first so the list, not the controls, shrinks.
        buttons = ttk.Frame(panel)
        buttons.pack(side="bottom", fill="x")
        form = ttk.Frame(panel)
        form.pack(side="bottom", fill="x", pady=8)
        tree = table(panel, [("part", "부품 종류", 290), ("size", "사이즈", 120),
                             ("capacity", "용량", 130), ("source", "근거", 120)], 4)
        tree.configure(selectmode="browse")

        def row(part):
            meta = self.part_metadata(part)
            return (part, meta.get("size") or "미인식", format_capacitance(meta.get("capacitance_pf")) or "미인식",
                    SOURCE_LABELS.get(meta.get("source"), "미인식"))

        for part in sorted(self.capacities):
            tree.insert("", "end", iid=part, values=row(part))
        size, capacity = tk.StringVar(), tk.StringVar()
        for column, (label, value) in enumerate((("사이즈 (예: 1005M / 0402)", size),
                                                 ("용량 (예: 100nF, 4.7uF · 숫자만 쓰면 pF)", capacity))):
            ttk.Label(form, text=label).grid(row=0, column=column, sticky="w")
            ttk.Entry(form, textvariable=value).grid(row=1, column=column, sticky="ew", padx=(0, 12))
            form.columnconfigure(column, weight=1)

        def select(_event=None):
            if tree.selection():
                meta = self.part_metadata(tree.selection()[0])
                size.set(meta.get("size") or "")
                capacity.set(editable_capacitance(meta.get("capacitance_pf")))

        def apply(reset=False):
            if not tree.selection():
                messagebox.showinfo("부품 정보", "목록에서 부품 종류를 먼저 선택하세요.", parent=dialog)
                return
            part = tree.selection()[0]
            try:
                if reset:
                    self.metadata_overrides.pop(part, None)
                else:
                    value = parse_capacitance_input(capacity.get()) if capacity.get().strip() else None
                    fields = validate_component_metadata(dict(size=size.get().strip() or None, capacitance_pf=value))
                    self.metadata_overrides[part] = dict(fields, source="Manual")
                self.capacities[part] = self.part_metadata(part).get("capacitance_pf")
                for item in self.priority_table.get_children():
                    if self.priority_table.item(item, "values")[0] == part:
                        self.priority_table.item(item, values=(part, self.capacity_label(part)))
                self.sort_current_priority()
                self.refresh_views()
                self.invalidate()
                tree.item(part, values=row(part))
                select()
            except ValueError as exc:
                messagebox.showerror("부품 정보", str(exc), parent=dialog)

        tree.bind("<<TreeviewSelect>>", select)
        ttk.Button(buttons, text="불러온 정보로 복원", command=lambda: apply(True)).pack(side="left")
        ttk.Button(buttons, text="수동 값 적용", command=apply, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="닫기", command=dialog.destroy).pack(side="right", padx=8)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def build_menu(self):
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="SPD 열기…", accelerator="Ctrl+O", command=self.choose_spd)
        files.add_command(label="SPD 다른 이름으로 저장…", accelerator="Ctrl+S", command=self.save)
        files.add_separator()
        files.add_command(label="종료", command=self.close)
        menu.add_cascade(label="파일", menu=files)
        self.edit_menu = tk.Menu(menu, tearoff=False)
        self.edit_menu.add_command(label="실행 취소", accelerator="Ctrl+Z", command=self.undo)
        self.edit_menu.add_command(label="다시 실행", accelerator="Ctrl+Y", command=self.redo)
        menu.add_cascade(label="편집", menu=self.edit_menu)
        menu.add_command(label="도움말", command=self.help)
        self.configure(menu=menu)

    def help(self):
        show_help(self, asset_root() / "assets" / "help.html")

    def build_files(self, parent):
        box = ttk.LabelFrame(parent, text="공통 SPD 파일 · 두 탭이 같은 작업 상태를 사용합니다", padding=8)
        box.pack(fill="x")
        buttons = ttk.Frame(box)
        buttons.pack(side="right")
        ttk.Button(buttons, text="SPD 열기…", command=self.choose_spd).pack(side="left")
        self.scope_button = ttk.Button(buttons, text="거리 기준 · 대상…", state="disabled", command=self.configure_spd)
        self.scope_button.pack(side="left", padx=6)
        self.metadata_button = ttk.Button(buttons, text="부품 정보…", state="disabled", command=self.edit_component_metadata)
        self.metadata_button.pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="합성 예제", command=self.load_examples).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="도움말  F1", command=self.help).pack(side="left")
        self.file_label = tk.StringVar(value="SPD 열기… 로 파일을 선택하거나 합성 예제로 사용법을 확인하세요.")
        label = ttk.Label(box, textvariable=self.file_label, justify="left")
        label.pack(side="left", fill="x", expand=True)
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(event.width - 4, 100)))

    def confirm_discard(self):
        return self.snapshot() == self._saved_state or messagebox.askyesno(
            "작업 변경", "저장하지 않은 작업을 버리고 계속하시겠습니까?", default="no", parent=self)

    def close(self):
        if self.confirm_discard():
            self.destroy()

    def choose_spd(self):
        path = filedialog.askopenfilename(title="Cadence Sigrity SPD 선택", parent=self,
                                          filetypes=[("Cadence Sigrity SPD", "*.spd"), ("모든 파일", "*.*")])
        if path:
            self.configure_spd(path)

    def configure_spd(self, path=None):
        if path and not self.confirm_discard():
            return
        try:
            layout = open_spd(self, path) if path else self.spd_layout
            choice = SpdScopeDialog(self, layout, None if path else self.spd_selection).result
            if not choice:
                return
            if not path:
                if choice[0] == self.spd_selection[0] and set(choice[1]) == set(self.spd_selection[1]):
                    return True
                if not messagebox.askyesno("범위 변경 · 새 작업", "범위를 변경하면 현재 작업을 종료하고 처음 연 SPD에서 다시 시작합니다.\n저장한 변경을 이어가려면 저장된 SPD를 여세요. 계속하시겠습니까?", default="no", parent=self):
                    return
            # The dialog already measured every distance for this scope.
            records = choice[2] if len(choice) > 2 and choice[2] else None
            self.load_layout(path or self.paths["spd"], layout, *choice[:2], records=records)
            return True
        except Exception as exc:
            messagebox.showerror("SPD 불러오기", str(exc), parent=self)

    def load_layout(self, path, layout, reference, targets, records=None):
        if records is None:
            records = layout.records(reference, targets)
        geometry = layout.geometry()
        metadata = layout.scope_metadata(reference, targets)
        included = {r["refdes"] for r in records} | {r["refdes"] for r in layout.inventory if r["component"] == reference}
        geometry = {ref: {**values, **metadata.get(ref, {})} for ref, values in geometry.items() if ref in included}
        self._tracking = False
        self.board.set_records([], {})
        self.board.query.set("")
        self.paths, self.hashes = {"spd": str(path)}, {"spd": layout.source_hash}
        self.spd_layout, self.spd_selection = layout, (reference, tuple(targets))
        self.board_geometry = geometry
        self.metadata_overrides = {}
        self.manual, self.aliases, self.policies = [], {}, []
        self._pristine_manual = []
        self.alias_label.set("등록된 별칭 없음")
        self.rearrange.set(False)
        self.use_policies.set(False)
        # Values typed for the previous file must not leak into the new one.
        self.scope_search.set("")
        self.net_input.set("")
        self.component_input.set("")
        self.expression_input.delete(0, "end")
        self.show_records(records)
        self.render_commands()
        self.scope_button.configure(state="normal")
        self.metadata_button.configure(state="normal")
        self.file_label.set(f"{Path(path).name} · 기준 {reference} · 대상 {len(targets)}종 · {len(records):,}자리")
        self.title(f"{Path(path).name} — Decap Adjustment {__version__}")
        self._undo.clear()
        self._redo.clear()
        self._last_state = self.snapshot()
        self._saved_state = deepcopy(self._last_state)
        self._tracking = True
        self.update_history_menu()
        self.notice.set(f"{len(records):,}개 decap을 불러왔습니다. 거리 단위: µm")

    def show_records(self, records):
        self.records = deepcopy(records)
        self.capacities = {r["component"]: self.part_metadata(r["component"]).get("capacitance_pf") for r in records}
        self.net_list.delete(0, "end")
        for net in sorted({r["net"] for r in records if r["net"]}):
            self.net_list.insert("end", net)
        self.reset_priority()
        self.render_policies()
        self.refresh_views()

    def load_examples(self):
        if not self.confirm_discard():
            return
        try:
            path = asset_root() / "examples" / "board.spd"
            layout = read_spd(path)
            self.load_layout(path, layout, "DEMO_DUT", [c for c in layout.components if c.startswith("CAP_")])
            commands = asset_root() / "examples" / "commands.xlsx"
            self._tracking = False
            self.manual = read_commands(commands, inspect_workbook(commands, "commands")[0])
            self.render_commands()
            self.invalidate()
            # The bundled commands are the example's starting point, not unsaved user work.
            self._pristine_manual = deepcopy(self.manual)
            self._last_state = self.snapshot()
            self._saved_state = deepcopy(self._last_state)
            self._tracking = True
            self.notice.set("합성 예제를 불러왔습니다. '계산 · 미리보기'로 예제 명령의 결과를 확인하세요.")
        except Exception as exc:
            self._tracking = True
            messagebox.showerror("예제", str(exc), parent=self)

    def build_commands(self):
        page = self.pages[1]
        # Only the two tables carry weight: a short window shrinks them evenly and never
        # pushes the entry row or the action buttons out of view.
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        page.rowconfigure(4, weight=1)
        search = ttk.Frame(page)
        search.grid(row=0, column=0, sticky="ew")
        ttk.Label(search, text="채널 / SITE / NET / 부품 검색").pack(side="left")
        self.scope_search = tk.StringVar()
        ttk.Entry(search, textvariable=self.scope_search).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(search, text="명령 Excel 가져오기…", command=self.import_commands).pack(side="right")
        self.scope_table = table(page, [("site", "SITE", 90), ("channel", "채널 / NET", 210),
                                      ("component", "Component", 250), ("active", "활성", 65),
                                      ("disabled", "비활성", 65), ("total", "전체", 65)], 3, grid=dict(row=1, column=0))
        self.scope_table.configure(selectmode="browse")
        self.scope_table.bind("<<TreeviewSelect>>", self.select_scope)
        # Double-click or Enter moves straight to typing the command for the chosen row.
        for event in ("<Double-1>", "<Return>"):
            self.scope_table.bind(event, lambda _event: self.expression_input.focus_set())
        self.scope_search.trace_add("write", lambda *_: self.refresh_scope())
        ttk.Label(page, text="위 표에서 행을 선택한 뒤 명령을 입력하세요.   A: 가까운 순 · D: 먼 순  |  +N: 활성화 · -N: 비활성화  |  예: A-2,D+1",
                  style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 4))
        row = ttk.Frame(page)
        row.grid(row=3, column=0, sticky="ew")
        self.net_input = ttk.Combobox(row, width=22)
        self.component_input = ttk.Combobox(row, width=28)
        self.expression_input = ttk.Entry(row, width=16)
        for index, (label, widget) in enumerate((("NET", self.net_input), ("Component", self.component_input), ("명령", self.expression_input))):
            ttk.Label(row, text=label).grid(row=0, column=index, sticky="w")
            widget.grid(row=1, column=index, sticky="ew", padx=(0, 8))
            row.columnconfigure(index, weight=1)
        ttk.Button(row, text="추가", command=self.add_command).grid(row=1, column=3, padx=2)
        ttk.Button(row, text="선택 수정", command=self.edit_command).grid(row=1, column=4, padx=2)
        ttk.Button(row, text="선택 삭제", command=self.delete_commands).grid(row=1, column=5, padx=2)
        self.command_table = table(page, [("net", "NET", 210), ("component", "Component", 260), ("expression", "명령", 180)], 3,
                                   grid=dict(row=4, column=0))
        self.command_table.bind("<<TreeviewSelect>>", self.select_command)
        self.command_table.bind("<Delete>", lambda event: self.delete_commands())
        self.expression_input.bind("<Return>", lambda event: self.add_command())
        box = ttk.Frame(page)
        box.grid(row=5, column=0, sticky="ew", pady=(5, 0))
        self.alias_label = tk.StringVar(value="등록된 별칭 없음")
        ttk.Button(box, text="별칭 편집…", command=self.edit_aliases).pack(side="right")
        self.preview_button = ttk.Button(box, text="계산 · 미리보기", command=self.preview, style="Primary.TButton")
        self.preview_button.pack(side="right", padx=8)
        alias = ttk.Label(box, textvariable=self.alias_label, justify="left")
        alias.pack(side="left", fill="x", expand=True)
        alias.bind("<Configure>", lambda event: alias.configure(wraplength=max(event.width - 4, 100)))

    def refresh_scope(self):
        if not hasattr(self, "scope_table"):
            return
        self.scope_table.delete(*self.scope_table.get_children())
        groups = {}
        for row in self.records:
            site = self.board_geometry.get(row["refdes"], {}).get("site", "")
            key = (site, row["net"], row["component"])
            amounts = groups.setdefault(key, [0, 0, 0])
            amounts[0] += effective_status(row) == "Automatic"
            amounts[1] += effective_status(row) == "Disabled"
            amounts[2] += 1
        terms = self.scope_search.get().casefold().split()
        for key, counts in sorted(groups.items()):
            if all(term in " ".join(key).casefold() for term in terms):
                self.scope_table.insert("", "end", values=(*key, *counts))
        self.net_input.configure(values=sorted({r["net"] for r in self.records}))
        self.component_input.configure(values=sorted({r["component"] for r in self.records}))

    def select_scope(self, _event=None):
        selected = self.scope_table.selection()
        if selected:
            values = self.scope_table.item(selected[0], "values")
            self.net_input.set(values[1])
            self.component_input.set(values[2])

    def select_command(self, _event=None):
        selected = self.command_table.selection()
        if selected:
            values = self.command_table.item(selected[0], "values")
            self.net_input.set(values[0])
            self.component_input.set(values[1])
            self.expression_input.delete(0, "end")
            self.expression_input.insert(0, values[2])

    def render_commands(self):
        self.command_table.delete(*self.command_table.get_children())
        for index, command in enumerate(self.manual):
            self.command_table.insert("", "end", iid=str(index), values=[command[k] for k in ("net", "component", "expression")])

    def command_input(self, replacing=None):
        if not self.records:
            raise ValueError("SPD 파일을 먼저 불러오세요.")
        net, comp, expr = self.net_input.get().strip(), self.component_input.get().strip(), self.expression_input.get().strip()
        actual = self.aliases.get(comp, comp)
        if not any(r["net"] == net for r in self.records) or actual not in self.capacities:
            raise ValueError("불러온 SPD의 NET과 Component를 선택하세요. 위 표에서 행을 선택하면 자동으로 채워집니다.")
        # Queue the normalised spelling so the list and the Excel import read alike.
        expr = ",".join(f"{direction}{delta:+d}" for direction, delta in parse_expression(expr))
        for index, command in enumerate(self.manual):
            if index != replacing and (command["net"], self.aliases.get(command["component"], command["component"])) == (net, actual):
                raise ValueError(f"{net} / {actual} 명령이 이미 목록에 있습니다 ({command['expression']}).\n"
                                 "해당 행을 선택한 뒤 '선택 수정'으로 A-2,D+1처럼 쉼표로 이어서 입력하세요.")
        return dict(net=net, component=comp, expression=expr, source="직접 입력")

    def add_command(self):
        try:
            self.manual.append(self.command_input())
            self.render_commands()
            self.expression_input.delete(0, "end")
            self.command_table.see(str(len(self.manual) - 1))
            self.invalidate()
        except ValueError as exc:
            messagebox.showerror("명령", str(exc), parent=self)

    def edit_command(self):
        selected = self.command_table.selection()
        if len(selected) != 1:
            messagebox.showinfo("명령", "수정할 명령을 아래 목록에서 한 행만 선택하세요.", parent=self)
            return
        try:
            self.manual[int(selected[0])] = self.command_input(replacing=int(selected[0]))
            self.render_commands()
            self.invalidate()
        except ValueError as exc:
            messagebox.showerror("명령", str(exc), parent=self)

    def delete_commands(self):
        selected = self.command_table.selection()
        if not selected:
            return
        for index in sorted((int(i) for i in selected), reverse=True):
            self.manual.pop(index)
        self.render_commands()
        self.invalidate()

    def import_commands(self):
        path = filedialog.askopenfilename(title="증감 명령 Excel", parent=self,
                                          filetypes=[("Excel 통합 문서", "*.xlsx"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            mapping = MappingDialog(self, path, "commands").result
            if mapping:
                commands = read_commands(path, mapping)
                self.manual.extend(commands)
                self.render_commands()
                self.invalidate()
                self.notice.set(f"{Path(path).name}에서 명령 {len(commands):,}개를 목록에 추가했습니다.")
        except Exception as exc:
            messagebox.showerror("명령 가져오기", str(exc), parent=self)

    def snapshot(self):
        return deepcopy(dict(records=self.records, manual=self.manual, aliases=self.aliases, policies=self.policies,
                             capacities=self.capacities, metadata_overrides=self.metadata_overrides, priority=[self.priority_table.item(i, "values")[0] for i in self.priority_table.get_children()],
                             rearrange=self.rearrange.get(), use_policies=self.use_policies.get(),
                             selected_nets=[self.net_list.get(i) for i in self.net_list.curselection()]))

    def remember(self):
        if not self._tracking:
            return
        state = self.snapshot()
        if state != self._last_state:
            self._undo.append(self._last_state)
            self._redo.clear()
            self._last_state = state
        self.update_history_menu()

    def update_history_menu(self):
        if hasattr(self, "edit_menu"):
            self.edit_menu.entryconfigure(0, state="normal" if self._undo else "disabled")
            self.edit_menu.entryconfigure(1, state="normal" if self._redo else "disabled")

    def restore(self, state):
        self._tracking = False
        state = deepcopy(state)
        for key in ("records", "manual", "aliases", "policies", "capacities", "metadata_overrides"):
            setattr(self, key, state[key])
        self.rearrange.set(state["rearrange"])
        self.use_policies.set(state["use_policies"])
        self.render_policies()
        self.priority_table.delete(*self.priority_table.get_children())
        for part in state["priority"]:
            self.priority_table.insert("", "end", values=(part, self.capacity_label(part)))
        self.net_list.selection_clear(0, "end")
        for index, net in enumerate(self.net_list.get(0, "end")):
            if net in state["selected_nets"]:
                self.net_list.selection_set(index)
        self.alias_label.set("; ".join(f"{k} → {v}" for k, v in self.aliases.items()) or "등록된 별칭 없음")
        self.render_commands()
        self.refresh_views()
        self.invalidate()
        self._last_state = self.snapshot()
        self._tracking = True
        self.update_history_menu()

    def undo(self, _event=None):
        if self._undo:
            self._redo.append(self.snapshot())
            self.restore(self._undo.pop())
            self.notice.set("실행 취소 · 두 탭에 반영했습니다.")
        return "break"

    def redo(self, _event=None):
        if self._redo:
            self._undo.append(self.snapshot())
            self.restore(self._redo.pop())
            self.notice.set("다시 실행 · 두 탭에 반영했습니다.")
        return "break"

    def refresh_views(self):
        self.refresh_scope()
        if hasattr(self, "board"):
            parts = {component: self.part_metadata(component) for component in {r["component"] for r in self.records}}
            rows = [dict(r, size=parts[r["component"]].get("size"),
                         capacitance_pf=parts[r["component"]].get("capacitance_pf"),
                         metadata_source=SOURCE_LABELS.get(parts[r["component"]].get("source"), "")) for r in self.records]
            self.board.set_records(rows, self.board_geometry)
        if hasattr(self, "save_button"):
            self.save_button.configure(state="normal" if self.records else "disabled")

    def set_preview_message(self, message):
        if hasattr(self, "preview_label"):
            self.preview_label.set(message)

    def write_details(self, text=None):
        """Replace (text given) or clear the read-only verification log."""
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if text:
            self.details.insert("end", text)
        self.details.configure(state="disabled")

    def invalidate(self, *_):
        self.result = None
        if hasattr(self, "apply_button"):
            self.apply_button.configure(state="disabled")
        if hasattr(self, "change_table"):
            # Every invalidation resets the headline: an older result, apply or error text
            # would otherwise describe a state that an edit or an undo has just replaced.
            self.set_preview_message("계산 전입니다. '계산 · 미리보기'로 현재 상태를 계산하세요.")
            for tree in (self.change_table, self.summary_table):
                tree.delete(*tree.get_children())
            self.write_details()
        if self._tracking:
            self.remember()

    def apply_preview(self):
        if not self.result or not self.result["ok"]:
            return
        try:
            self.spd_layout.validate_records(self.result["records"])
        except ValueError as exc:
            messagebox.showerror("SPD 적용 불가", str(exc), parent=self)
            return
        changed = len(self.result["changes"])
        self.records = deepcopy(self.result["records"])
        self.manual = []
        self.rearrange.set(False)
        self.render_commands()
        self.render_policies()
        self.refresh_views()
        self.invalidate()
        self.set_preview_message(f"적용 완료 · {changed:,}개 RefDes 변경을 두 탭에 반영했습니다. 명령 목록은 비웠습니다.")
        self.notice.set(f"명령 결과를 적용했습니다 ({changed:,}개 RefDes). 'SPD 다른 이름으로 저장…'으로 새 파일에 기록하세요. Ctrl+Z로 되돌릴 수 있습니다.")

    def apply_selection(self, refdes, status):
        if status not in ADJUSTABLE_STATUSES or not refdes:
            return
        chosen = [r for r in self.records if r["refdes"] in refdes]
        if len(chosen) != len(refdes) or any(effective_status(r) not in ADJUSTABLE_STATUSES for r in chosen):
            messagebox.showerror("선택 적용", "현재 조정 가능한 decap을 선택하세요.", parent=self)
            return
        for row in chosen:
            if effective_status(row) != status:
                row["status"] = status
        self.refresh_views()
        self.invalidate()
        self.notice.set(f"선택 {len(chosen):,}자리 {status} 적용 · 수량 탭에 반영했습니다.")

    def edit_aliases(self):
        def convert(text):
            aliases = {}
            for entry in text.replace(";", "\n").splitlines():
                if not entry.strip():
                    continue
                if "=" not in entry:
                    raise ValueError(f"{entry.strip()!r}: '명령표 이름 = 실제 부품명' 형식으로 입력하세요.")
                key, target = (part.strip() for part in entry.split("=", 1))
                if not key or not target:
                    raise ValueError(f"{entry.strip()!r}: 별칭 이름과 실제 부품명을 모두 입력하세요.")
                if key in aliases:
                    raise ValueError(f"별칭 {key!r}이(가) 중복되었습니다.")
                if self.records and target not in self.capacities:
                    raise ValueError(f"{target!r}은(는) 불러온 SPD의 조정 대상 부품이 아닙니다. 정확한 부품명을 입력하세요.")
                aliases[key] = target
            return aliases

        aliases = ask_text(self, "명시적 별칭",
                           "명령표의 부품 이름이 SPD와 다를 때 연결합니다. 한 줄에 하나씩 입력하세요.\n"
                           "형식: 명령표 이름 = 실제 부품명      (모두 지우면 별칭을 삭제합니다)",
                           "\n".join(f"{k} = {v}" for k, v in self.aliases.items()), multiline=True, convert=convert)
        if aliases is None:
            return
        self.aliases = aliases
        self.alias_label.set("; ".join(f"{k} → {v}" for k, v in aliases.items()) or "등록된 별칭 없음")
        self.invalidate()

    def build_layout(self):
        page = self.pages[2]
        # Both sections carry weight so a short window shrinks them together; with pack the
        # upper one kept its full height and the rule list below collapsed to nothing.
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        page.rowconfigure(2, weight=1)
        # The explanation shares the checkbox line and the rule buttons share theirs: on a
        # 768-pixel-high screen every saved line is a visible list row.
        head = ttk.Frame(page)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.rearrange = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="명령 처리 후 부품·상태 재배치", variable=self.rearrange, command=self.invalidate).pack(side="left")
        note = ttk.Label(head, text="용량/우선순위에 따라 Component와 Status 쌍을 이동합니다. 선택 NET·부품만 대상이며 비활성 자리도 포함합니다.",
                         style="Muted.TLabel", justify="left")
        note.pack(side="left", fill="x", expand=True, padx=(14, 0))
        note.bind("<Configure>", lambda event: note.configure(wraplength=max(event.width - 4, 100)))
        upper = ttk.Frame(page)
        upper.grid(row=1, column=0, sticky="nsew")
        left = ttk.LabelFrame(upper, text="재배치 대상 NET (Ctrl/Shift로 선택)", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        net_buttons = ttk.Frame(left)
        net_buttons.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(net_buttons, text="모든 NET 선택", command=self.select_all_nets).pack(side="right")
        ttk.Button(net_buttons, text="선택 해제", command=self.clear_net_selection).pack(side="right", padx=6)
        self.net_list = tk.Listbox(left, selectmode="extended", exportselection=False, height=4, activestyle="none",
                                   highlightthickness=1, highlightbackground="#59616e", relief="flat")
        net_scroll = ttk.Scrollbar(left, orient="vertical", command=self.net_list.yview)
        self.net_list.configure(yscrollcommand=net_scroll.set)
        net_scroll.pack(side="right", fill="y")
        self.net_list.pack(fill="both", expand=True)
        self.net_list.bind("<<ListboxSelect>>", self.invalidate)
        right = ttk.LabelFrame(upper, text="가까운 자리 → 먼 자리 순서 / 선택 부품", padding=8)
        right.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(right)
        buttons.pack(side="bottom", fill="x")
        for text, command in [("↑", lambda: self.move_priority(-1)), ("↓", lambda: self.move_priority(1)),
                              ("용량 수정", self.edit_capacity), ("제외", self.exclude_priority), ("용량순 초기화", self.reset_priority)]:
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=2)
        self.priority_table = table(right, [("component", "Component", 240), ("capacity", "용량", 110)], 3, xscroll=False)
        self.priority_table.configure(selectmode="browse")
        lower = ttk.LabelFrame(page, text="부족한 + 명령의 연쇄 변환 규칙 (같은 NET · 작은 용량순 권장)", padding=8)
        lower.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.use_policies = tk.BooleanVar(value=False)
        bar = ttk.Frame(lower)
        bar.pack(side="top", fill="x")
        ttk.Checkbutton(bar, text="아래 변환 규칙 사용 (새 부품은 배치 목록에도 추가)", variable=self.use_policies, command=self.render_policies).pack(side="left")
        ttk.Button(bar, text="선택 규칙 삭제", command=self.remove_policy).pack(side="right")
        ttk.Button(bar, text="규칙 추가…", command=self.add_policy).pack(side="right", padx=6)
        ttk.Button(bar, text="용량순 연쇄 규칙 채우기", command=self.suggest_policies).pack(side="right")
        self.policy_table = table(lower, [("target", "추가할 부품", 200), ("donor", "가져올 부품", 200), ("rule", "자리 선택 / 보정", 350)], 2, xscroll=False)

    def select_all_nets(self):
        self.net_list.selection_set(0, "end")
        self.invalidate()

    def clear_net_selection(self):
        self.net_list.selection_clear(0, "end")
        self.invalidate()

    def reset_priority(self):
        self.priority_table.delete(*self.priority_table.get_children())
        for comp in sorted(self.capacities, key=lambda c: (self.capacities[c] is None, self.capacities[c] or 0, c)):
            self.priority_table.insert("", "end", values=(comp, self.capacity_label(comp)))
        self.invalidate()

    def move_priority(self, delta):
        selected = self.priority_table.selection()
        if selected:
            item = selected[0]
            self.priority_table.move(item, "", max(0, self.priority_table.index(item) + delta))
            self.invalidate()

    def exclude_priority(self):
        for item in self.priority_table.selection():
            self.priority_table.delete(item)
        self.invalidate()

    def edit_capacity(self):
        selected = self.priority_table.selection()
        if not selected:
            messagebox.showinfo("용량 수정", "용량을 수정할 부품을 목록에서 선택하세요.", parent=self)
            return
        item = selected[0]
        comp = self.priority_table.item(item, "values")[0]
        value = ask_text(self, "용량 수정", f"{comp}\n용량을 입력하세요. 예: 100nF, 4.7uF (숫자만 쓰면 pF)",
                         editable_capacitance(self.capacities.get(comp)), convert=parse_capacitance_input)
        if value is not None and math.isfinite(value):
            self.metadata_overrides[comp] = {**self.part_metadata(comp), "capacitance_pf": value, "source": "Manual"}
            self.capacities[comp] = value
            self.refresh_views()
            self.priority_table.item(item, values=(comp, self.capacity_label(comp)))
            self.sort_current_priority()
            self.invalidate()

    def render_policies(self):
        original = {r["component"] for r in self.records if effective_status(r) in ADJUSTABLE_STATUSES}
        allowed = original | ({p["target"] for p in self.policies} if self.use_policies.get() else set())
        self.capacities = {c: value for c, value in self.capacities.items() if c in allowed}
        for item in self.priority_table.get_children():
            if self.priority_table.item(item, "values")[0] not in allowed:
                self.priority_table.delete(item)
        added = False
        for component in sorted(allowed - set(self.capacities)):
            capacity = self.part_metadata(component).get("capacitance_pf")
            self.capacities[component] = capacity
            self.priority_table.insert("", "end", values=(component, self.capacity_label(component)))
            added = True
        if added:
            self.sort_current_priority()
        self.policy_table.delete(*self.policy_table.get_children())
        for p in self.policies:
            rule = f"{'먼 순' if p['direction'] == 'D' else '가까운 순'} / {p['donor_status']} / {'먼 Disabled 보정 · 부족 시 연쇄' if p['compensate'] else 'Automatic 변환 불가'}"
            self.policy_table.insert("", "end", values=(p["target"], p["donor"], rule))
        self.invalidate()

    def recommended_policies(self):
        by_cap = {}
        for comp, capacity in self.capacities.items():
            if capacity is not None:
                by_cap.setdefault(capacity, []).append(comp)
        policies = []
        capacities = sorted(by_cap, reverse=True)
        for target, donor in zip(capacities, capacities[1:]):
            if len(by_cap[target]) == len(by_cap[donor]) == 1:
                policies.append(dict(target=by_cap[target][0], donor=by_cap[donor][0], direction="D", donor_status="any", compensate=True))
        return policies

    def suggest_policies(self):
        self.policies = policies = self.recommended_policies()
        self.render_policies()
        messagebox.showinfo("연쇄 변환 규칙", f"큰 용량 → 바로 다음 작은 용량의 {len(policies)}개 규칙을 채웠습니다.\n"
                            "상태에 관계없이 먼 자리부터 가져오고, 활성 수량은 먼 Disabled에서 보정합니다.\n"
                            "자리나 보정분이 부족하면 다음 작은 용량으로 이어집니다.\n"
                            "목록을 확인한 뒤 '아래 변환 규칙 사용'을 선택하세요.\n"
                            "같은 용량의 부품이 여러 종류이면 수동으로 정확한 이름을 지정하세요.", parent=self)

    def add_policy(self):
        if not self.capacities:
            messagebox.showinfo("변환 규칙", "SPD 파일을 먼저 불러오세요.", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.title("부족분 변환 규칙 추가")
        dialog.transient(self)
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="+ 명령에 필요한 자리가 부족하면 '가져올 부품'의 자리를 '추가할 부품'으로 바꿉니다.").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        target = ttk.Combobox(panel, values=sorted(self.capacities), width=36)
        donor = ttk.Combobox(panel, values=sorted(self.capacities), width=36, state="readonly")
        direction = ttk.Combobox(panel, values=["D · 먼 자리부터", "A · 가까운 자리부터"], state="readonly", width=36)
        direction.current(0)
        status = ttk.Combobox(panel, values=["Disabled만", "Automatic과 Disabled 모두"], state="readonly", width=36)
        status.current(1)
        for row, (label, widget) in enumerate([("추가할 부품", target), ("가져올 부품", donor), ("자리 선택", direction), ("가져올 부품의 상태", status)], 1):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 12))
            widget.grid(row=row, column=1, sticky="w", pady=6)
        ttk.Label(panel, text="활성 수량은 먼 Disabled에서 보정하며, 부족하면 가져올 부품의 규칙으로 이어집니다.",
                  style="Muted.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=12)

        def accept(_event=None):
            target_name, donor_name = target.get().strip(), donor.get().strip()
            if not target_name or donor_name not in self.capacities or target_name == donor_name:
                messagebox.showerror("변환 규칙", "추가할 부품 이름과, 그와 다른 가져올 부품을 지정하세요.", parent=dialog)
                return
            if any(p["target"] == target_name for p in self.policies):
                messagebox.showerror("변환 규칙", "이미 같은 대상의 규칙이 있습니다. 기존 규칙을 삭제한 뒤 추가하세요.", parent=dialog)
                return
            self.policies.append(dict(target=target_name, donor=donor_name, direction=direction.get()[0], donor_status="Disabled" if status.current() == 0 else "any", compensate=True))
            self.render_policies()
            dialog.destroy()
            if not self.use_policies.get():
                self.notice.set("규칙을 추가했습니다. 계산에 쓰려면 '아래 변환 규칙 사용'을 켜세요.")

        buttons = ttk.Frame(panel)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="left", padx=8)
        ttk.Button(buttons, text="규칙 추가", command=accept, style="Primary.TButton").pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        center_window(dialog, self)
        dialog.deiconify()
        target.focus_set()
        dialog.grab_set()

    def sort_current_priority(self):
        items = list(self.priority_table.get_children())
        items.sort(key=lambda i: (self.capacities[self.priority_table.item(i, "values")[0]] is None,
                                  self.capacities[self.priority_table.item(i, "values")[0]] or 0,
                                  self.priority_table.item(i, "values")[0]))
        for index, item in enumerate(items):
            self.priority_table.move(item, "", index)

    def remove_policy(self):
        for index in sorted([self.policy_table.index(i) for i in self.policy_table.selection()], reverse=True):
            self.policies.pop(index)
        self.render_policies()

    def build_preview(self):
        page = self.pages[3]
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(2, 6))
        self.apply_button = ttk.Button(top, text="명령 결과 적용 · 두 탭에 반영", command=self.apply_preview, state="disabled", style="Primary.TButton")
        self.apply_button.pack(side="right", padx=(12, 0))
        self.preview_label = tk.StringVar(value="계산 전입니다. '계산 · 미리보기'로 현재 상태를 계산하세요.")
        headline = ttk.Label(top, textvariable=self.preview_label, font=(FONT_FAMILY, 12, "bold"), justify="left")
        headline.pack(side="left", fill="x", expand=True)
        headline.bind("<Configure>", lambda event: headline.configure(wraplength=max(event.width - 4, 100)))
        self.preview_tabs = notebook = ttk.Notebook(page)
        notebook.pack(fill="both", expand=True)
        change, summary, details = (ttk.Frame(notebook, padding=6) for _ in range(3))
        for frame, title in [(change, "RefDes 변경"), (summary, "NET · 부품 수량"), (details, "검증 · 선택 근거")]:
            notebook.add(frame, text=title)
        self.change_table = table(change, [("refdes", "RefDes", 90), ("net", "NET", 150), ("old_component", "기존 부품", 170),
                                           ("new_component", "결과 부품", 170), ("old_status", "기존 상태", 90),
                                           ("new_status", "결과 상태", 90), ("distance", "거리 (µm)", 90), ("reason", "근거", 460)], 4)
        self.summary_table = table(summary, [("net", "NET", 180), ("component", "Component", 220), ("before_total", "기존 자리", 80),
                                             ("after_total", "결과 자리", 80), ("before_active", "기존 활성", 80),
                                             ("expected_active", "예상 활성", 80), ("after_active", "결과 활성", 80)], 4)
        for tree, numeric in ((self.change_table, ("distance",)),
                              (self.summary_table, ("before_total", "after_total", "before_active", "expected_active", "after_active"))):
            for column in numeric:
                tree.column(column, anchor="e")
        # The reason column is wider than the view; a centred heading would sit off-screen.
        self.change_table.heading("reason", anchor="w")
        self.details = tk.Text(details, wrap="word", font=(FONT_FAMILY, 10), height=6, state="disabled",
                               relief="flat", padx=10, pady=8)
        self.details.tag_configure("error", foreground="#f0a8a0")
        self.details.tag_configure("warning", foreground="#e1af71")
        scroll = ttk.Scrollbar(details, command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.details.pack(fill="both", expand=True)

    def preview(self):
        self.invalidate()
        try:
            if not self.records or "spd" not in self.paths:
                raise ValueError("SPD 파일을 먼저 불러오세요.")
            if any(fingerprint(self.paths[k]) != h for k, h in self.hashes.items()):
                raise ValueError("입력 파일이 변경되었습니다. 파일을 다시 불러오세요.")
            commands = self.manual
            nets = [self.net_list.get(i) for i in self.net_list.curselection()]
            priority = [self.priority_table.item(i, "values")[0] for i in self.priority_table.get_children()]
            if not commands and not self.rearrange.get():
                raise ValueError("계산할 내용이 없습니다. '명령 목록'에 증감 명령을 추가하거나 '배치 · 변환'에서 재배치를 켜세요.")
            options = dict(priority=priority, rearrange=self.rearrange.get(), selected_nets=nets, aliases=self.aliases,
                           portable_statuses=WRITABLE_STATUSES)
            self.result = compute(self.records, commands, policies=self.policies if self.use_policies.get() else [], **options)
            commands, adjustments = self.choose_quantity_adjustments(commands)
            if commands is None:
                self.show_result()
                return
            if adjustments:
                self.result = compute(self.records, commands, policies=self.policies if self.use_policies.get() else [], **options)
            self.offer_shortage_rules(commands, options)
            for adjustment in adjustments:
                issue = dict(adjustment, applied=self.result["ok"])
                if not self.result["ok"]:
                    issue["message"] = "전체 취소됨 · " + issue["message"]
                self.result["issues"].append(issue)
            self.show_result()
        except Exception as exc:
            self.result = None
            self.set_preview_message("입력 오류 · 계산하지 않았습니다")
            self.write_details(str(exc))
            self.notice.set("입력과 설정을 확인하세요.")
            messagebox.showerror("계산할 수 없습니다", str(exc), parent=self)

    def additional_recommended_policies(self):
        # Do not enable unrelated saved rules when the user has switched them off.
        if self.policies and not self.use_policies.get():
            return []
        linked = {p["target"]: p["donor"] for p in self.policies}
        suggested = []
        for policy in self.recommended_policies():
            if policy["target"] in linked:
                continue
            donor, seen = policy["donor"], {policy["target"]}
            while donor in linked and donor not in seen:
                seen.add(donor)
                donor = linked[donor]
            if donor not in seen:
                linked[policy["target"]] = policy["donor"]
                suggested.append(policy)
        return suggested

    def choose_quantity_adjustments(self, commands):
        if self.result["ok"] or not any(i["code"] in ("shortage", "compensation_shortage") for i in self.result["issues"]):
            return commands, []
        policies = (self.policies if self.use_policies.get() else []) + self.additional_recommended_policies()
        adjustments = []
        while shortages := find_shortages(self.records, commands, self.aliases):
            shortage = shortages[0]
            choices = shortage_choices(self.records, commands, shortage, policies=policies, aliases=self.aliases)
            selected = QuantityShortageDialog(self, shortage, choices).result
            if selected is None:
                self.result["issues"].append(dict(level="info", code="quantity_adjustment_cancelled",
                                                  message="수량 조정을 취소했습니다. 이번 계산에서 선택한 조정안도 모두 적용하지 않습니다."))
                return None, []
            commands = selected["commands"]
            adjustments.append(selected["adjustment"])
        return commands, adjustments

    def offer_shortage_rules(self, commands, options):
        if self.result["ok"] or not any(i["code"] in ("shortage", "compensation_shortage") for i in self.result["issues"]):
            return
        self.result["issues"].append(dict(level="info", code="conversion_guidance", message=
            "'배치 · 변환'에서 부족한 부품과 그 원본 부품의 규칙이 이어지는지 확인하세요. "
            "연쇄의 마지막 용량에서도 자리나 활성 수량 보정분이 부족하면 계산할 수 없습니다. "
            "규칙을 사용할 때는 '아래 변환 규칙 사용'을 켜세요. "
            "같은 용량의 부품이 여러 종류이면 '규칙 추가'로 직접 지정해야 합니다."))
        suggested = self.additional_recommended_policies()
        if not suggested:
            return
        trial = compute(self.records, commands, policies=self.policies + suggested, **options)
        if not trial["ok"]:
            return
        conversions = [a for a in trial["actions"] if a["kind"] == "conversion"]
        needed = [p for p in suggested if any(a["new_component"] == p["target"] for a in conversions)]
        if not needed:
            return
        lines = []
        for policy in needed:
            changes = [a for a in conversions if a["new_component"] == policy["target"]]
            active = sum(a["new_status"] == "Automatic" for a in changes)
            disabled = len(changes) - active
            status = "Disabled만" if policy["donor_status"] == "Disabled" else "Automatic / Disabled"
            direction = "먼 자리부터" if policy["direction"] == "D" else "가까운 자리부터"
            compensation = "먼 Disabled에서 활성 수 보정 · 부족 시 연쇄 확보"
            lines.append(f"{policy['donor']} → {policy['target']}: {len(changes)}개 (Automatic {active}개 / Disabled {disabled}개)\n"
                         f"  같은 NET · {status} · {direction} · {compensation}")
        if messagebox.askyesno("부족분 변환 규칙 확인",
                "현재 부품의 Disabled 자리만으로는 + 명령을 채울 수 없습니다.\n"
                "아래 규칙을 추가하면 전체 계산과 수량 검증을 통과합니다.\n"
                "중간 용량의 Disabled 변환은 다음 용량으로 넘길 자리를 확보하는 단계입니다.\n\n" + "\n\n".join(lines) +
                "\n\n이 규칙을 켜고 미리보기를 진행할까요?\n"
                "원본 파일은 변경하지 않으며, 결과를 적용하기 전에 변경 목록을 확인할 수 있습니다.",
                parent=self, default="no"):
            self.policies.extend(needed)
            self.use_policies.set(True)
            self.render_policies()
            self.result = trial

    def show_result(self):
        if self.result["ok"]:
            try:
                self.spd_layout.validate_records(self.result["records"])
            except ValueError as exc:
                rejected = self.result
                self.result = compute(self.records, [])
                self.result.update(ok=False, issues=[*rejected["issues"], dict(level="error", code="spd_compatibility", message=str(exc))],
                                   actions=[dict(action, applied=False) for action in rejected["actions"]])
        result = self.result
        for row in result["changes"]:
            values = dict(row, distance=format_distance(row.get("distance")))
            self.change_table.insert("", "end", values=[values.get(c, "") for c in self.change_table["columns"]])
        for row in result["summary"]:
            self.summary_table.insert("", "end", values=[row.get(c, "") for c in self.summary_table["columns"]])
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        for issue in result["issues"]:
            level = issue["level"]
            self.details.insert("end", f"[{LEVEL_LABELS.get(level, level)}] {issue['message']}\n\n",
                                level if level in ("error", "warning") else ())
        self.details.insert("end", "선택/변환/보정 이력\n" if result["actions"] else "선택/변환/보정 이력 없음\n")
        for index, action in enumerate(result["actions"], 1):
            kind = {"command": "증감", "conversion": "변환", "compensation": "보정", "rearrange": "재배치"}.get(action.get("kind"), "처리")
            self.details.insert("end", f"{index}. [{kind}] {action.get('refdes', '')} · {action.get('net', '')} · {action.get('source', '')}\n"
                                f"{action.get('old_component', '')}/{action.get('old_status', '')} → {action.get('new_component', '')}/{action.get('new_status', '')}\n"
                                f"{action.get('reason', '')}{' (전체 취소됨)' if not action.get('applied', True) else ''}\n\n")
        self.details.configure(state="disabled")
        errors = [issue["message"] for issue in result["issues"] if issue["level"] == "error"]
        if result["ok"]:
            label = f"검증 통과 · {len(result['changes']):,}개 RefDes 변경 · {len(result['summary']):,}개 수량 조합"
            if not result["changes"]:
                label = "검증 통과 · 현재 상태에서 바뀌는 RefDes가 없습니다"
        else:
            label = "검증 실패 · 적용할 수 없습니다" + (f" — {errors[0]}" if errors else "")
        self.set_preview_message(label)
        self.notice.set("변경과 수량을 검토한 뒤 '명령 결과 적용 · 두 탭에 반영'을 누르세요." if result["ok"] else
                        "오류가 있어 결과를 적용할 수 없습니다. '검증 · 선택 근거' 탭에서 원인을 확인하세요.")
        self.apply_button.configure(state="normal" if result["ok"] and result["changes"] else "disabled")
        self.tabs.select(0)
        self.command_tabs.select(self.pages[3])
        # A failure is explained in the log; a success is reviewed in the change list.
        self.preview_tabs.select(0 if result["ok"] else 2)

    def save_directory(self):
        """Folder offered for the new SPD: the last one used, else beside the source."""
        directory = getattr(self, "_save_directory", None)
        if directory is None:
            directory = Path(self.paths["spd"]).parent
            if directory == asset_root() / "examples":
                # The bundled example lives in the read-only installation folder.
                documents = Path.home() / "Documents"
                directory = documents if documents.is_dir() else Path.home()
        return directory

    def save(self):
        if not self.records or self.spd_layout is None:
            self.notice.set("저장할 SPD가 없습니다. 먼저 SPD 파일을 여세요.")
            return
        if self.manual or (self.result and self.result["ok"] and self.result["changes"]):
            if not messagebox.askyesno("적용된 상태 저장", "아직 적용하지 않은 명령 또는 미리보기가 있습니다.\n현재 두 탭에 적용된 상태만 저장하시겠습니까?", default="no", parent=self):
                return
        directory, stem, rejected = self.save_directory(), Path(self.paths["spd"]).stem + "_adjusted", None
        while True:
            # Existing files are never replaced, so the app explains that itself instead of
            # letting the system dialog offer an overwrite that would then be refused.
            path = filedialog.asksaveasfilename(title="적용된 상태를 새 SPD로 저장", parent=self, defaultextension=".spd",
                                                confirmoverwrite=False, initialdir=str(directory),
                                                initialfile=unused_name(directory, stem, ".spd"),
                                                filetypes=[("Cadence Sigrity SPD", "*.spd")])
            if not path:
                return
            path = Path(path)
            if not os.path.lexists(path):
                break
            messagebox.showwarning("다른 이름 필요", f"{path.name}\n\n이미 있는 파일은 덮어쓰지 않습니다. 새 파일 이름을 지정하세요.", parent=self)
            if path == rejected:
                return
            directory, rejected = path.parent, path
        try:
            overrides = {part: {key: values.get(key) for key in ("size", "capacitance_pf")}
                         for part, values in self.metadata_overrides.items()}
            saved = run_spd_task(self, "SPD 저장 · 재검증", "새 파일을 기록하고 다시 읽어 검증하고 있습니다.",
                                 lambda: save_spd(self.paths["spd"], self.spd_layout, self.records, path,
                                                  component_metadata=overrides))
            self._save_directory = Path(saved).parent
            self._saved_state = self.snapshot()
            # Pending instructions are not persisted by an SPD document, so a queue the user
            # built still counts as unsaved; the untouched commands of a bundled example do not.
            if self.manual != self._pristine_manual:
                self._saved_state["manual"] = []
            self.notice.set(f"저장 완료: {saved}")
            messagebox.showinfo("SPD 저장 완료", f"{saved}\n\n원본 SPD는 보존했습니다. 이 창의 작업은 계속 원본 기준입니다.\n"
                                "저장한 파일에서 이어서 작업하려면 그 파일을 여세요.", parent=self)
        except OSError as exc:
            messagebox.showerror("저장 실패", f"파일을 기록하지 못했습니다. 폴더의 쓰기 권한과 경로를 확인하세요.\n\n{exc}", parent=self)
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self)


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DecapAdjustment.Desktop.1")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except OSError:
            pass
    if "--self-test" in sys.argv:
        from .self_test import self_test
        index = sys.argv.index("--self-test")
        report = self_test()
        if len(sys.argv) > index + 1:
            Path(sys.argv[index + 1]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    app = App()
    if "--smoke-test" in sys.argv:
        app.update()
        index = sys.argv.index("--smoke-test")
        if len(sys.argv) > index + 1:
            Path(sys.argv[index + 1]).write_text(json.dumps({"ok": True, "version": __version__, "window": app.title(), "tabs": len(app.tabs.tabs())}), encoding="utf-8")
        app.destroy()
        return
    app.mainloop()
