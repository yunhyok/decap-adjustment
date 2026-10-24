"""Exercise explicit quantity choices in the real Tk dialog."""

import os
import sys
import tkinter as tk
from tkinter import ttk

import pytest

from decap_manager.gui import QuantityShortageDialog


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="Tk requires a display",
)


@pytest.fixture
def quantity_data():
    components = [
        dict(component="CAP_100NF", total=3, active=2, requested_active=2),
        dict(component="CAP_1UF", total=2, active=2, requested_active=2),
        dict(component="CAP_4U7F", total=1, active=1, requested_active=3),
    ]
    shortage = dict(net="SYNTHETIC", total=6, active=5, requested_active=7, deficit=1, components=components)
    choices = []
    for index, expected in enumerate([(2, 2, 2), (1, 2, 3), (2, 1, 3)]):
        choices.append(dict(label=f"Synthetic option {index + 1}", description=f"Explicit description for option {index + 1}",
                            commands=[], adjustment=dict(level="info", code="quantity_adjustment", message=f"Choice {index + 1}"),
                            summary=[dict(net="SYNTHETIC", component=row["component"], before_total=row["total"],
                                          after_total=row["total"], before_active=row["active"], expected_active=value, after_active=value)
                                     for row, value in zip(components, expected)]))
    return shortage, choices


@pytest.fixture
def inspect_quantity_dialog(monkeypatch):
    parent = tk.Tk()
    parent.withdraw()

    def inspect(shortage, choices, check):
        def wait_window(dialog, *_args, **_kwargs):
            try:
                dialog.transient("")
                minimum_width, minimum_height = dialog.minsize()
                dialog.geometry(f"{minimum_width}x{minimum_height}+0+0")
                dialog.deiconify()
                dialog.update()
                check(dialog)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()

        monkeypatch.setattr(tk.Toplevel, "wait_window", wait_window)
        return QuantityShortageDialog(parent, shortage, choices)

    yield inspect
    parent.destroy()


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _amounts(dialog):
    return {dialog.amounts.set(item, "component"):
            tuple(dialog.amounts.set(item, field) for field in ("total", "active", "requested", "adjusted"))
            for item in dialog.amounts.get_children()}


def test_quantity_dialog_has_no_default_and_updates_selected_numbers(quantity_data, inspect_quantity_dialog):
    shortage, choices = quantity_data

    def check(dialog):
        assert dialog.result is None and not dialog.choices_table.selection()
        assert dialog.apply_button.instate(["disabled"])
        assert _amounts(dialog) == {"CAP_100NF": ("3", "2", "2", "—"),
                                    "CAP_1UF": ("2", "2", "2", "—"),
                                    "CAP_4U7F": ("1", "1", "3", "—")}
        dialog.accept()
        assert dialog.winfo_exists() and dialog.result is None
        for index in (0, 1, 2):
            dialog.choices_table.selection_set(str(index))
            dialog.choices_table.event_generate("<<TreeviewSelect>>")
            dialog.update()
            assert dialog.detail.get() == choices[index]["description"]
            assert not dialog.apply_button.instate(["disabled"])
            assert {component: int(values[-1]) for component, values in _amounts(dialog).items()} == {
                row["component"]: row["after_active"] for row in choices[index]["summary"]}
        assert dialog.apply_button.winfo_ismapped()
        assert dialog.apply_button.winfo_rooty() + dialog.apply_button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
        dialog.apply_button.invoke()

    dialog = inspect_quantity_dialog(shortage, choices, check)
    assert dialog.result is choices[2]


@pytest.mark.parametrize("cancel_method", ["button", "escape", "window_close"])
def test_quantity_dialog_cancel_paths_never_apply_selection(quantity_data, inspect_quantity_dialog, cancel_method):
    shortage, choices = quantity_data

    def check(dialog):
        dialog.choices_table.selection_set("1")
        dialog.select_choice()
        assert not dialog.apply_button.instate(["disabled"])
        if cancel_method == "button":
            next(widget for widget in _walk(dialog) if isinstance(widget, ttk.Button) and widget.cget("text") == "취소").invoke()
        elif cancel_method == "escape":
            dialog.focus_force()
            dialog.update()
            dialog.event_generate("<Escape>")
            dialog.update()
        else:
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
        assert not dialog.winfo_exists()

    dialog = inspect_quantity_dialog(shortage, choices, check)
    assert dialog.result is None


def test_quantity_dialog_without_valid_options_cannot_apply(quantity_data, inspect_quantity_dialog):
    shortage, _ = quantity_data

    def check(dialog):
        assert not dialog.choices_table.get_children()
        assert dialog.apply_button.instate(["disabled"])
        assert "없습니다" in dialog.detail.get()
        dialog.accept()
        assert dialog.result is None and dialog.winfo_exists()

    assert inspect_quantity_dialog(shortage, [], check).result is None
