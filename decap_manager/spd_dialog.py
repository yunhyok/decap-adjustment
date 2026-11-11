"""Choose the physical reference and capacitor types for a text SPD."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .spd import read_spd


def run_spd_task(parent, title, detail, action):
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.protocol("WM_DELETE_WINDOW", lambda: None)
    box = ttk.Frame(window, padding=20)
    box.pack(fill="both", expand=True)
    ttk.Label(box, text=detail).pack(anchor="w")
    progress = ttk.Progressbar(box, mode="indeterminate", length=330)
    progress.pack(fill="x", pady=(12, 0))
    progress.start()
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


class SpdScopeDialog(simpledialog.Dialog):
    def __init__(self, parent, layout, selection=None):
        self.layout = layout
        self.selection = selection
        super().__init__(parent, title="SPD · 거리 기준과 조정 대상")

    def body(self, frame):
        names = sorted(self.layout.components)
        ttk.Label(frame, text="거리 기준 부품", font=("Malgun Gothic", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="DUT·LGA 등 기준 핀이 있는 부품 종류를 선택하세요.").pack(anchor="w")
        self.reference = ttk.Combobox(frame, values=names, state="readonly", width=46)
        self.reference.pack(fill="x", pady=(3, 12))
        ttk.Label(frame, text="여러 종류 선택: Ctrl 또는 Shift + 클릭").pack(anchor="w")
        group = ttk.Frame(frame)
        group.pack(fill="both", expand=True, pady=(3, 6))
        self.targets = ttk.Treeview(group, columns=("type", "count"), show="headings", height=9, selectmode="extended")
        self.targets.heading("type", text="조정할 커패시터 종류")
        self.targets.heading("count", text="자리 수")
        self.targets.column("type", width=300)
        self.targets.column("count", width=75, anchor="e")
        scroll = ttk.Scrollbar(group, command=self.targets.yview)
        self.targets.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.targets.pack(fill="both", expand=True)
        for name in names:
            item = self.targets.insert("", "end", values=(name, self.layout.components[name]))
            selected = name in self.selection[1] if self.selection else (
                name.upper().startswith("CAP") or self.layout.component_metadata.get(name, {}).get("capacitance_pf") is not None)
            if selected:
                self.targets.selection_add(item)
        references = [name for name in names if any(token in name.upper() for token in ("DUT", "LGA", "BUMP"))]
        if self.selection:
            self.reference.set(self.selection[0])
        elif len(references) == 1:
            self.reference.set(references[0])
        ttk.Label(frame, text="같은 NET의 가장 가까운 기준 핀까지 거리(µm)를 계산합니다.").pack(anchor="w")
        return self.reference

    def buttonbox(self):
        box = ttk.Frame(self, padding=(8, 4, 8, 10))
        box.pack(fill="x")
        ttk.Button(box, text="취소", command=self.cancel).pack(side="right", padx=4)
        ttk.Button(box, text="적용", command=self.ok).pack(side="right", padx=4)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        reference = self.reference.get()
        targets = tuple(str(self.targets.set(item, "type")) for item in self.targets.selection())
        try:
            if not reference or not targets or reference in targets:
                raise ValueError("기준 부품과 서로 다른 조정 대상 부품을 한 종류 이상 선택하세요.")
            records = self.layout.records(reference, targets)
        except ValueError as exc:
            messagebox.showerror("SPD 설정", str(exc), parent=self)
            return False
        self.result = (reference, targets, records)
        return True
