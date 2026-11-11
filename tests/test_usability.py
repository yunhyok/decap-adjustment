"""Regression tests for the 2.1.0 correctness and usability fixes."""

import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font, ttk
from types import SimpleNamespace

import pytest

from decap_manager import spd
from decap_manager.engine import (compute, editable_capacitance, effective_status, format_capacitance,
                                  parse_capacitance_input)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "board.spd"
needs_display = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                                   reason="Tk requires a display")


def test_capacitance_input_accepts_units_and_bare_picofarads():
    for text, expected in {"100000": 100_000, "100nF": 100_000, "4.7uF": 4_700_000, "4.7 µF": 4_700_000,
                           "2.2μF": 2_200_000, "0.1u": 100_000, "47p": 47, "22 pF": 22, "1mF": 1e9,
                           "1e-7F": 100_000, "1,000,000": 1_000_000, ".5uF": 500_000}.items():
        assert parse_capacitance_input(text) == pytest.approx(expected), text
    for text in ("", "   ", "abc", "0", "-5", "nan", "1e999", "10 xF", "4u7"):
        with pytest.raises(ValueError):
            parse_capacitance_input(text)


def test_capacitance_text_is_readable_and_reads_back_exactly():
    assert format_capacitance(4_700_000) == "4.7 µF"
    assert format_capacitance(100_000.0) == "100 nF"
    assert format_capacitance(22) == "22 pF"
    assert format_capacitance(None) == format_capacitance(0) == format_capacitance(float("nan")) == ""
    assert editable_capacitance(None) == ""
    for value in (10_000.0, 4_700_000.0, 123_456_789.0, 1 / 3, 4_700_000.0000001, 1e15):
        assert parse_capacitance_input(editable_capacitance(value)) == value


def _unknown_usage_layout(tmp_path):
    text = EXAMPLE.read_text(encoding="utf-8")
    marked = text.replace(".Connect C1 CAP_0603M_10NF\n", ".Connect C1 CAP_0603M_10NF Usage = 0b1\n")
    assert marked != text
    path = tmp_path / "unknown.spd"
    path.write_text(marked, encoding="utf-8")
    layout = spd.read_spd(path)
    targets = [name for name in layout.components if name.startswith("CAP_")]
    return path, layout, targets, layout.records("DEMO_DUT", targets)


def test_rearranging_an_unrecognised_usage_stays_writable(tmp_path):
    path, layout, targets, records = _unknown_usage_layout(tmp_path)
    assert records[0]["status"] == "Unknown"
    options = dict(rearrange=True, selected_nets=["U0_DEMO_VDD"],
                   priority=["CAP_0603M_4U7F", "CAP_0603M_1UF", "CAP_0603M_100NF", "CAP_0603M_10NF"])
    verbatim = compute(records, [], **options)
    assert verbatim["ok"]
    with pytest.raises(ValueError, match="Unknown"):
        layout.validate_records(verbatim["records"])

    result = compute(records, [], portable_statuses=spd.WRITABLE_STATUSES, **options)
    assert result["ok"], result["issues"]
    moved = [new for old, new in zip(records, result["records"])
             if (old["component"], old["status"]) != (new["component"], new["status"])]
    assert moved and all(row["status"] in spd.WRITABLE_STATUSES for row in moved)
    for component in targets:
        before = sum(effective_status(r) == "Automatic" for r in records if r["component"] == component)
        after = sum(effective_status(r) == "Automatic" for r in result["records"] if r["component"] == component)
        assert before == after
    layout.validate_records(result["records"])
    saved = spd.save_spd(path, layout, result["records"], tmp_path / "rearranged.spd")
    assert spd.read_spd(saved).records("DEMO_DUT", targets) == result["records"]


def test_unrecognised_usage_stays_on_its_own_slot_when_nothing_moves_it(tmp_path):
    _, layout, _, records = _unknown_usage_layout(tmp_path)
    # Smallest first is already the bundled order, so C1 keeps its part and its source value.
    result = compute(records, [], rearrange=True, selected_nets=["U0_DEMO_VDD"], portable_statuses=spd.WRITABLE_STATUSES,
                     priority=["CAP_0603M_10NF"])
    assert result["ok"] and result["records"][0]["status"] == "Unknown" and not result["changes"]


@pytest.fixture
def app(monkeypatch):
    from decap_manager.gui import App
    instance = App()
    instance.withdraw()
    monkeypatch.setattr("decap_manager.gui.messagebox.showinfo", lambda *a, **k: None)
    yield instance
    instance.destroy()


@pytest.fixture
def example(app, monkeypatch):
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror",
                        lambda title, error, **k: pytest.fail(f"{title}: {error}"))
    app.load_examples()
    return app


def _fully_inside(app, widget):
    return (widget.winfo_viewable() and widget.winfo_height() >= widget.winfo_reqheight()
            and widget.winfo_rooty() >= app.winfo_rooty()
            and widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height()
            and widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width())


@needs_display
def test_save_and_action_buttons_stay_visible_at_the_minimum_window_size(example):
    app = example
    width, height = app.minsize()
    app.deiconify()
    app.geometry(f"{width}x{height}+0+0")
    app.update()
    assert _fully_inside(app, app.save_button)
    assert _fully_inside(app, app.preview_button)
    app.preview()
    app.update()
    assert _fully_inside(app, app.save_button) and _fully_inside(app, app.apply_button)
    app.tabs.select(1)
    app.update()
    assert _fully_inside(app, app.save_button)
    assert _fully_inside(app, app.board.activate_button) and _fully_inside(app, app.board.disable_button)


@needs_display
def test_window_and_row_height_follow_the_display(app):
    from decap_manager.theme import FONT_FAMILY, fit_window
    linespace = font.Font(root=app, family=FONT_FAMILY, size=10).metrics("linespace")
    assert int(ttk.Style(app).lookup("Treeview", "rowheight")) > linespace
    window = tk.Toplevel(app)
    requested = []
    window.geometry = requested.append
    fit_window(window, 50_000, 40_000, 30_000, 20_000)
    size, x, y = requested[0].split("+")
    width, height = map(int, size.split("x"))
    assert width < app.winfo_screenwidth() and height < app.winfo_screenheight()
    assert int(x) >= 0 and int(y) >= 0
    assert window.minsize() == (width, height)
    window.destroy()


@needs_display
def test_part_information_needs_a_file_and_shortcuts_ignore_caps_lock(app, monkeypatch):
    assert app.metadata_button.instate(["disabled"]) and app.scope_button.instate(["disabled"])
    for sequence in ("<Control-z>", "<Control-Z>", "<Control-y>", "<Control-Y>", "<Control-Shift-Z>",
                     "<Control-s>", "<Control-S>", "<Control-o>", "<Control-O>"):
        assert app.bind(sequence), sequence
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror",
                        lambda title, error, **k: pytest.fail(f"{title}: {error}"))
    app.save()
    assert "SPD" in app.notice.get()
    app.load_examples()
    assert app.metadata_button.instate(["!disabled"]) and app.scope_button.instate(["!disabled"])
    assert app.title().startswith("board.spd")


@needs_display
def test_bundled_example_is_not_unsaved_work_and_new_files_reset_inputs(example, monkeypatch):
    app = example
    monkeypatch.setattr("decap_manager.gui.messagebox.askyesno", lambda *a, **k: pytest.fail("unexpected prompt"))
    assert app.manual and not app._undo and app.confirm_discard()
    app.scope_search.set("no such row")
    app.net_input.set("STALE_NET")
    app.component_input.set("STALE_PART")
    app.expression_input.insert(0, "A+1")
    app.board.query.set("no such decap")
    app.update()
    assert not app.scope_table.get_children() and not app.board.tree.get_children()
    app.load_layout(app.paths["spd"], app.spd_layout, *app.spd_selection)
    app.update()
    assert app.scope_search.get() == app.net_input.get() == app.component_input.get() == ""
    assert app.expression_input.get() == app.board.query.get() == ""
    assert app.scope_table.get_children() and app.board.tree.get_children()


@needs_display
def test_duplicate_manual_command_is_refused_and_spelling_normalised(app, monkeypatch):
    errors = []
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda title, message, **k: errors.append(message))
    app.add_command()
    assert errors and "SPD" in errors.pop()
    app.load_examples()
    app.manual = []
    app.render_commands()
    app.invalidate()
    app.net_input.set("U0_DEMO_VDD")
    app.component_input.set("CAP_0603M_10NF")
    app.expression_input.insert(0, " a-1 , d + 2 ")
    app.add_command()
    assert not errors and app.manual[0]["expression"] == "A-1,D+2"
    assert app.expression_input.get() == ""
    app.expression_input.insert(0, "A+1")
    app.add_command()
    assert len(app.manual) == 1 and "이미" in errors.pop()
    app.command_table.selection_set("0")
    app.expression_input.delete(0, "end")
    app.expression_input.insert(0, "D+1")
    app.edit_command()
    assert not errors and app.manual[0]["expression"] == "D+1"


@needs_display
def test_alias_editor_explains_bad_lines(example, monkeypatch):
    app = example
    captured = {}

    def prompt(parent, title, message, initial="", *, multiline=False, convert=None):
        captured.update(convert=convert, multiline=multiline)
        return {"OLD_NAME": "CAP_0603M_10NF"}

    monkeypatch.setattr("decap_manager.gui.ask_text", prompt)
    app.edit_aliases()
    assert app.aliases == {"OLD_NAME": "CAP_0603M_10NF"} and captured["multiline"]
    assert "OLD_NAME → CAP_0603M_10NF" in app.alias_label.get()
    convert = captured["convert"]
    for text, reason in (("OLD_NAME", "형식"), ("= CAP_0603M_10NF", "모두"),
                         ("A = CAP_0603M_10NF\nA = CAP_0603M_1UF", "중복"), ("A = NOT_A_PART", "조정 대상")):
        with pytest.raises(ValueError, match=reason):
            convert(text)
    assert convert("A = CAP_0603M_10NF; B=CAP_0603M_1UF\n\n") == {"A": "CAP_0603M_10NF", "B": "CAP_0603M_1UF"}
    assert convert("") == {}


@needs_display
def test_failed_preview_states_the_reason_and_success_reports_the_apply(example, monkeypatch):
    app = example
    monkeypatch.setattr("decap_manager.gui.messagebox.askyesno", lambda *a, **k: False)
    queued = list(app.manual)
    app.manual = [dict(net="U0_DEMO_VDD", component="CAP_0603M_10NF", expression="A+3", source="직접 입력")]
    app.render_commands()
    app.invalidate()
    app.preview()
    assert not app.result["ok"] and app.apply_button.instate(["disabled"])
    assert app.preview_tabs.index("current") == 2
    assert "검증 실패" in app.preview_label.get() and "부족" in app.preview_label.get()
    assert "[오류]" in app.details.get("1.0", "end")
    assert str(app.details.cget("state")) == "disabled"

    app.manual = queued
    app.render_commands()
    app.invalidate()
    app.preview()
    assert app.result["ok"] and app.preview_tabs.index("current") == 0
    assert app.change_table.set(app.change_table.get_children()[0], "distance") == "3,354.1"
    app.apply_preview()
    assert "적용 완료" in app.preview_label.get() and "6" in app.preview_label.get()
    assert app.apply_button.instate(["disabled"]) and not app.change_table.get_children()


@needs_display
def test_save_refuses_existing_names_itself_and_proposes_an_unused_one(example, monkeypatch, tmp_path):
    app = example
    app.manual = []
    app.render_commands()
    app.invalidate()
    existing = tmp_path / "taken.spd"
    existing.write_text("keep me", encoding="utf-8")
    target = tmp_path / "board_adjusted.spd"
    answers, asked, warnings = [str(existing), str(target), ""], [], []

    def choose(**options):
        asked.append(options)
        return answers[len(asked) - 1]

    monkeypatch.setattr("decap_manager.gui.filedialog.asksaveasfilename", choose)
    monkeypatch.setattr("decap_manager.gui.messagebox.showwarning", lambda title, message, **k: warnings.append(message))
    app.save()
    assert len(asked) == 2 and len(warnings) == 1 and "taken.spd" in warnings[0]
    assert all(options["confirmoverwrite"] is False for options in asked)
    assert asked[1]["initialdir"] == str(tmp_path)
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert spd.read_spd(target).records(*app.spd_selection) == app.records
    app.save()
    assert asked[2]["initialdir"] == str(tmp_path) and asked[2]["initialfile"] == "board_adjusted_2.spd"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["board_adjusted.spd", "taken.spd"]


@needs_display
def test_text_prompt_keeps_invalid_input_open(monkeypatch):
    from decap_manager.dialogs import ask_text
    root = tk.Tk()
    root.withdraw()
    seen = []

    def wait_window(dialog, *_args, **_kwargs):
        try:
            assert dialog.field.get() == "10 nF"
            dialog.field.delete(0, "end")
            dialog.field.insert(0, "abc")
            dialog.accept()
            seen.append((dialog.winfo_exists(), dialog.error.get()))
            dialog.field.delete(0, "end")
            dialog.field.insert(0, "4.7uF")
            dialog.accept()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    monkeypatch.setattr(tk.Toplevel, "wait_window", wait_window)
    try:
        assert ask_text(root, "용량", "입력", "10 nF", convert=parse_capacitance_input) == 4_700_000
        assert seen[0][0] and "해석" in seen[0][1]
    finally:
        root.destroy()


@needs_display
def test_scope_dialog_preselects_summarises_and_validates(monkeypatch):
    from decap_manager.spd_dialog import SpdScopeDialog
    from decap_manager.theme import apply_theme
    root = tk.Tk()
    root.withdraw()
    apply_theme(root)
    layout = spd.read_spd(EXAMPLE)
    errors, seen = [], {}
    monkeypatch.setattr("decap_manager.spd_dialog.messagebox.showerror", lambda title, message, **k: errors.append(message))

    def wait_window(dialog, *_args, **_kwargs):
        try:
            seen.update(chosen=dialog._chosen(), summary=dialog.summary.get())
            rows = dialog.targets.get_children()
            dialog.targets.selection_set(rows)
            dialog.update()
            seen["all"] = dialog.summary.get()
            assert not dialog.validate()
            dialog.targets.selection_set(())
            assert not dialog.validate()
            dialog.targets.selection_set([row for row in rows if dialog.targets.set(row, "type").startswith("CAP_")])
            seen["capacity"] = dialog.targets.set(dialog.targets.selection()[0], "capacity")
            dialog.ok()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    monkeypatch.setattr(tk.Toplevel, "wait_window", wait_window)
    try:
        result = SpdScopeDialog(root, layout).result
    finally:
        root.destroy()
    reference, targets = seen["chosen"]
    assert reference == "DEMO_DUT" and len(targets) == 4 and all(name.startswith("CAP_") for name in targets)
    assert "4종" in seen["summary"] and "32자리" in seen["summary"] and "6종" in seen["all"]
    assert seen["capacity"] == "100 nF"
    assert len(errors) == 2 and "DEMO_DUT" in errors[0] and "한 종류 이상" in errors[1]
    assert result[0] == "DEMO_DUT" and set(result[1]) == set(targets) and len(result[2]) == 32


@needs_display
def test_help_window_is_reused():
    from decap_manager.help_viewer import DEFAULT_HELP, show_help
    root = tk.Tk()
    root.withdraw()
    try:
        first = show_help(root, DEFAULT_HELP)
        assert show_help(root, DEFAULT_HELP) is first
        first.destroy()
        root.update()
        assert show_help(root, DEFAULT_HELP) is not first
    finally:
        root.destroy()


@needs_display
def test_board_lists_distance_sorts_and_keeps_the_reference_in_view():
    from decap_manager.board_view import BoardView
    root = tk.Tk()
    root.geometry("980x640")
    view = BoardView(root, lambda *_: None)
    view.pack(fill="both", expand=True)
    rows = [dict(refdes=ref, net="N", component="CAP", status="Automatic", distance=distance)
            for ref, distance in (("C1", 300.04), ("C2", 100.0), ("C10", 200.0))]
    rows.append(dict(refdes="C11", net="N", component="CAP", status="Disabled"))
    geometry = {ref: dict(x=x, y=100, side=side, pins=[(x - 10, 100), (x + 10, 100)])
                for ref, x, side in (("C1", 100, "Top"), ("C2", 300, "Top"), ("C10", 500, "Top"), ("C11", 300, "Bottom"))}
    geometry["U1"] = dict(x=0, y=0, side="Top", pins=[(0, 0), (20, 0)])
    try:
        view.set_records(rows, geometry)
        root.update()
        assert view.tree.set("C1", "distance") == "300.0"
        assert view.tree.get_children() == ("C1", "C2", "C10")
        view._sort_by("distance")
        assert view.tree.get_children() == ("C2", "C10", "C1") and "▲" in view.tree.heading("distance", "text")
        view._sort_by("distance")
        assert view.tree.get_children() == ("C1", "C10", "C2") and "▼" in view.tree.heading("distance", "text")
        view._sort_by("distance")
        assert view.tree.get_children() == ("C1", "C2", "C10") and view.tree.heading("distance", "text") == "거리 (µm)"
        view._sort_by("refdes")
        view._sort_by("refdes")
        assert view.tree.get_children() == ("C10", "C2", "C1")
        view._sort_by("refdes")

        view._select_all()
        assert view._selected == {"C1", "C2", "C10"}
        view._select(set())
        root.update()
        # Tk cannot synthesise a double click, so the bound handler is called with the row position.
        assert view.tree.bind("<Double-1>")
        x, y, width, height = view.tree.bbox("C10")
        view._locate(SimpleNamespace(x=x + 5, y=y + height // 2))
        root.update()
        assert view._scale == pytest.approx(view._fit_scale * 6)
        assert view._centers["C10"] == pytest.approx((view.canvas.winfo_width() / 2, view.canvas.winfo_height() / 2), abs=1)

        view.side_buttons["Bottom"].invoke()
        root.update()
        assert view.tree.get_children() == ("C11",) and set(view._centers) == {"C11"}
        assert view.tree.set("C11", "distance") == "—"
        pads = [item for item in view.canvas.find_withtag("board")
                if view.canvas.type(item) == "rectangle" and item not in view._items]
        assert len(pads) == 2
    finally:
        root.destroy()
