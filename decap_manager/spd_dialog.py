"""Choose the physical reference and capacitor types for a text SPD."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .engine import format_capacitance
from .spd import read_spd
from .theme import FONT_FAMILY, center_window, fit_window, px


def run_spd_task(parent, title, detail, action):
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window.transient(parent)
    window.resizable(False, False)
    window.protocol("WM_DELETE_WINDOW", lambda: None)
    box = ttk.Frame(window, padding=20)
    box.pack(fill="both", expand=True)
    ttk.Label(box, text=detail, justify="left").pack(anchor="w")
    progress = ttk.Progressbar(box, mode="indeterminate", length=px(window, 330))
    progress.pack(fill="x", pady=(12, 0))
    progress.start()
    center_window(window, parent)
    window.deiconify()
    window.grab_set()
    with ThreadPoolExecutor(max_workers=1) as worker:
        task = worker.submit(action)

        def poll():
            if task.done():
                window.destroy()
            else:
                window.after(100, poll)

        window.after(100, poll)
        parent.wait_window(window)
        return task.result()


def open_spd(parent, path):
    return run_spd_task(parent, "SPD 읽기", f"{Path(path).name}\n부품 연결과 핀 좌표를 읽고 있습니다.", lambda: read_spd(path))


class SpdScopeDialog(tk.Toplevel):
    """Pick the distance reference part and the capacitor part types to adjust."""

    def __init__(self, parent, layout, selection=None):
        super().__init__(parent)
        self.withdraw()
        self.title("SPD · 거리 기준과 조정 대상")
        self.transient(parent)
        self.layout = layout
        self.selection = selection
        self.result = None
        fit_window(self, 620, 620, 500, 460, parent)
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        names = sorted(layout.components)
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(buttons, text="적용", command=self.ok, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="취소", command=self.cancel).pack(side="right", padx=8)
        self.summary = tk.StringVar()
        ttk.Label(buttons, textvariable=self.summary, style="Muted.TLabel").pack(side="left")
        ttk.Label(frame, text="거리 기준 부품", font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="DUT·LGA 등 기준 핀이 있는 부품 종류를 선택하세요.", style="Muted.TLabel").pack(anchor="w")
        self.reference = ttk.Combobox(frame, values=[f"{name}   ({layout.components[name]:,}개)" for name in names],
                                      state="readonly", height=16)
        self._reference_names = names
        self.reference.pack(fill="x", pady=(3, 12))
        ttk.Label(frame, text="조정할 커패시터 종류", font=(FONT_FAMILY, 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="여러 종류 선택: Ctrl 또는 Shift + 클릭 · Ctrl+A 전체 선택", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text="같은 NET의 가장 가까운 기준 핀까지 거리(µm)를 계산합니다.",
                  style="Muted.TLabel").pack(side="bottom", anchor="w", pady=(6, 0))
        group = ttk.Frame(frame)
        group.pack(fill="both", expand=True, pady=(3, 0))
        self.targets = ttk.Treeview(group, columns=("type", "count", "capacity", "size"), show="headings",
                                    height=6, selectmode="extended")
        for key, label, width, anchor in (("type", "부품 종류", 270, "w"), ("count", "자리 수", 70, "e"),
                                          ("capacity", "용량", 90, "e"), ("size", "사이즈", 80, "center")):
            self.targets.heading(key, text=label, anchor=anchor)
            self.targets.column(key, width=px(self, width), minwidth=px(self, 60), anchor=anchor, stretch=key == "type")
        scroll = ttk.Scrollbar(group, command=self.targets.yview)
        self.targets.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.targets.pack(fill="both", expand=True)
        first = None
        for name in names:
            metadata = layout.component_metadata.get(name, {})
            item = self.targets.insert("", "end", values=(
                name, f"{layout.components[name]:,}", format_capacitance(metadata.get("capacitance_pf")) or "—",
                metadata.get("size") or "—"))
            selected = name in selection[1] if selection else (
                name.upper().startswith("CAP") or metadata.get("capacitance_pf") is not None)
            if selected:
                self.targets.selection_add(item)
                first = first or item
        if first:
            self.targets.see(first)
        references = [name for name in names if any(token in name.upper() for token in ("DUT", "LGA", "BUMP"))]
        if selection:
            self.reference.current(names.index(selection[0]))
        elif len(references) == 1:
            self.reference.current(names.index(references[0]))
        self.targets.bind("<<TreeviewSelect>>", self._summarize)
        for key in "aA":
            self.targets.bind(f"<Control-{key}>", self._select_all)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._summarize()
        self.deiconify()
        self.reference.focus_set()
        self.grab_set()
        self.wait_window()

    def _select_all(self, _event=None):
        self.targets.selection_set(self.targets.get_children())
        return "break"

    def _chosen(self):
        index = self.reference.current()
        reference = self._reference_names[index] if index >= 0 else ""
        targets = tuple(str(self.targets.set(item, "type")) for item in self.targets.selection())
        return reference, targets

    def _summarize(self, _event=None):
        _, targets = self._chosen()
        slots = sum(self.layout.components[name] for name in targets)
        self.summary.set(f"대상 {len(targets):,}종 · {slots:,}자리" if targets else "조정 대상을 선택하세요")

    def validate(self):
        reference, targets = self._chosen()
        try:
            if not reference:
                raise ValueError("거리 기준 부품을 선택하세요.")
            if not targets:
                raise ValueError("조정할 커패시터 종류를 한 종류 이상 선택하세요.")
            if reference in targets:
                raise ValueError(f"기준 부품 {reference}은(는) 조정 대상에서 빼야 합니다. 목록에서 Ctrl + 클릭으로 선택을 해제하세요.")
            self.configure(cursor="watch")
            self.update_idletasks()
            records = self.layout.records(reference, targets)
        except ValueError as exc:
            messagebox.showerror("SPD 설정", str(exc), parent=self)
            return False
        finally:
            self.configure(cursor="")
        self.result = (reference, targets, records)
        return True

    def ok(self, _event=None):
        if self.validate():
            self.destroy()

    def cancel(self, _event=None):
        self.result = None
        self.destroy()
