from copy import deepcopy
import os
import sys

import pytest

from decap_manager.engine import effective_status
from decap_manager.gui import App, asset_root
from decap_manager.spd import read_spd

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="Tk requires a display")


@pytest.fixture
def app(monkeypatch):
    instance = App()
    instance.withdraw()
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda title, error, **k: pytest.fail(f"{title}: {error}"))
    monkeypatch.setattr("decap_manager.gui.messagebox.showinfo", lambda *a, **k: None)
    instance.load_examples()
    yield instance
    instance.destroy()


def test_two_tabs_share_apply_undo_redo_and_spd_save(app, monkeypatch, tmp_path):
    assert len(app.tabs.tabs()) == 2
    before = deepcopy(app.records)
    app.preview()
    assert app.result["ok"] and app.records == before
    expected = deepcopy(app.result["records"])
    app.apply_preview()
    assert app.records == expected and not app.manual
    target = next(r for r in app.records if effective_status(r) == "Automatic")
    app.apply_selection({target["refdes"]}, "Disabled")
    assert target["status"] == "Disabled"
    app.undo()
    assert app.records == expected
    app.undo()
    assert app.records == before and app.manual
    app.redo()
    app.redo()
    assert next(r for r in app.records if r["refdes"] == target["refdes"])["status"] == "Disabled"
    out = tmp_path / "adjusted.spd"
    monkeypatch.setattr("decap_manager.gui.filedialog.asksaveasfilename", lambda **k: str(out))
    app.save()
    assert read_spd(out).records(*app.spd_selection) == app.records
    assert app.confirm_discard()


def test_search_command_edit_delete_and_history(app):
    app.manual = []
    app.render_commands()
    app.invalidate()
    app.scope_search.set("SITE1 100NF")
    items = app.scope_table.get_children()
    assert len(items) == 1
    app.scope_table.selection_set(items[0])
    app.select_scope()
    app.expression_input.insert(0, "A-1")
    app.add_command()
    assert app.manual[0]["net"] == "U1_DEMO_VDD"
    app.command_table.selection_set("0")
    app.expression_input.delete(0, "end")
    app.expression_input.insert(0, "D+1")
    app.edit_command()
    assert app.manual[0]["expression"] == "D+1"
    app.undo()
    assert app.manual[0]["expression"] == "A-1"
    app.redo()
    app.command_table.selection_set("0")
    app.delete_commands()
    assert not app.manual
    app.undo()
    assert app.manual[0]["expression"] == "D+1"
    app.apply_selection({app.records[0]["refdes"]}, "Disabled")
    assert not app._redo


def test_board_change_invalidates_preview_and_changes_next_counts(app):
    app.preview()
    assert app.result["ok"]
    app.apply_selection({"C3"}, "Automatic")
    assert app.result is None and app.apply_button.instate(["disabled"])
    app.manual = [dict(net="U0_DEMO_VDD", component="CAP_0603M_10NF", expression="D-1")]
    app.preview()
    row = next(r for r in app.result["summary"] if r["net"] == "U0_DEMO_VDD" and r["component"] == "CAP_0603M_10NF")
    assert (row["before_active"], row["after_active"]) == (3, 2)


def test_failed_preview_never_applies(app):
    before = deepcopy(app.records)
    app.manual = [dict(net="U0_DEMO_VDD", component="CAP_0603M_10NF", expression="A-99")]
    app.preview()
    assert not app.result["ok"]
    app.apply_preview()
    assert app.records == before


def test_manual_part_metadata_save_roundtrip_and_undo(app, monkeypatch, tmp_path):
    part = "CAP_0603M_10NF"
    original = deepcopy(app.part_metadata(part))
    app.metadata_overrides[part] = dict(size="1005M", capacitance_pf=20000.0, source="Manual")
    app.capacities[part] = 20000.0
    app.refresh_views()
    app.invalidate()
    app.undo()
    assert app.part_metadata(part) == original
    app.redo()
    assert app.part_metadata(part)["capacitance_pf"] == 20000
    app.manual = []
    app.render_commands()
    app.invalidate()
    out = tmp_path / "metadata.spd"
    monkeypatch.setattr("decap_manager.gui.filedialog.asksaveasfilename", lambda **k: str(out))
    app.save()
    restored = read_spd(out)
    assert restored.component_metadata[part]["size"] == "1005M"
    assert restored.component_metadata[part]["capacitance_pf"] == 20000


def test_same_scope_keeps_applied_edits_after_save(app, monkeypatch, tmp_path):
    from types import SimpleNamespace
    app.preview()
    app.apply_preview()
    current = deepcopy(app.records)
    out = tmp_path / "saved.spd"
    monkeypatch.setattr("decap_manager.gui.filedialog.asksaveasfilename", lambda **k: str(out))
    app.save()
    monkeypatch.setattr("decap_manager.gui.SpdScopeDialog", lambda *a: SimpleNamespace(result=(*app.spd_selection, [])))
    app.configure_spd()
    assert app.records == current
    monkeypatch.setattr("decap_manager.gui.SpdScopeDialog", lambda *a: SimpleNamespace(result=(app.spd_selection[0], app.spd_selection[1][:1], [])))
    prompts = []
    monkeypatch.setattr("decap_manager.gui.messagebox.askyesno", lambda *a, **k: prompts.append(a) or False)
    app.configure_spd()
    assert prompts and app.records == current


def test_unwriteable_preview_is_rejected_before_apply(app):
    original = deepcopy(app.records)
    app.spd_layout.validate_records = lambda rows: (_ for _ in ()).throw(ValueError("incompatible model"))
    app.preview()
    assert not app.result["ok"] and app.apply_button.instate(["disabled"])
    assert app.result["records"] == original and not app.result["changes"]
    assert any(i["code"] == "spd_compatibility" for i in app.result["issues"])


def test_metadata_dialog_rejects_unserializable_size_before_applying(app, monkeypatch):
    from tkinter import ttk
    errors = []
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda *a, **k: errors.append(a))
    app.edit_component_metadata()
    dialog = next(w for w in app.winfo_children() if w.winfo_class() == "Toplevel")
    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)
    children = list(walk(dialog))
    tree = next(w for w in children if isinstance(w, ttk.Treeview))
    part = tree.get_children()[0]
    tree.selection_set(part)
    dialog.update()
    entries = [w for w in children if isinstance(w, ttk.Entry)]
    apply = next(w for w in children if isinstance(w, ttk.Button) and w.cget("text") == "수동 값 적용")
    before = app.snapshot()
    for value in ("x" * 33, "0402\u200b"):
        entries[0].delete(0, "end")
        entries[0].insert(0, value)
        apply.invoke()
        assert app.snapshot() == before
    assert len(errors) == 2
    entries[0].delete(0, "end")
    entries[0].insert(0, "1005M")
    apply.invoke()
    assert app.part_metadata(part)["size"] == "1005M"
    dialog.destroy()
