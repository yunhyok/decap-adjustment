import json
import os
import sys

import pytest
from openpyxl import load_workbook

from decap_manager.engine import compute
from decap_manager.excel_io import export_spd_results
from decap_manager.gui import App, SpdScopeDialog, asset_root
from decap_manager.spd import read_spd


BOARD = asset_root() / "examples" / "board.spd"


@pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="Tk requires a display")
def test_spd_dialog_preview_save_and_return_to_excel(monkeypatch, tmp_path):
    app = App()
    app.withdraw()
    errors = []
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda title, error, **k: errors.append(error))
    monkeypatch.setattr("decap_manager.gui.messagebox.showinfo", lambda *a, **k: None)
    monkeypatch.setattr("decap_manager.gui.filedialog.askdirectory", lambda **k: str(tmp_path))

    def accept_scope():
        dialog = next((w for w in app.winfo_children() if isinstance(w, SpdScopeDialog)), None)
        if dialog is None:
            app.after(30, accept_scope)
        else:
            dialog.ok()

    try:
        app.load_examples()
        app.preview()
        expected = app.result["changes"]
        app.source.set("spd")
        app.switch_source()
        assert app.result is None and not app.records and "disabled" in app.save_button.state()
        app.after(50, accept_scope)
        app.load_examples()
        assert set(app.paths) == {"spd", "commands"} and len(app.records) == 32
        app.preview()
        assert app.result["changes"] == expected
        app.save()
        audit, = tmp_path.glob("decap_*/audit.json")
        assert json.loads(audit.read_text(encoding="utf-8"))["spd_source"]["sha256"] == app.spd_layout.source_hash
        app.source.set("excel")
        app.switch_source()
        assert set(app.paths) == {"commands"} and not app.records and app.result is None
        app.load_examples()
        app.preview()
        assert app.result["changes"] == expected and not errors
    finally:
        app.destroy()


def test_spd_export_keeps_unselected_rows_literal_and_refuses_stale_source(tmp_path):
    board = tmp_path / "layout.spd"
    board.write_bytes(BOARD.read_bytes().replace(b"DEMO_DUT", b"=DEMO_DUT"))
    layout = read_spd(board)
    targets = {"CAP_0603M_10NF"}
    records = layout.records("=DEMO_DUT", targets)
    result = compute(records, [dict(net="U0_DEMO_VDD", component="CAP_0603M_10NF", expression="A+1")])
    output = tmp_path / "export"
    args = board, layout, "=DEMO_DUT", targets, result, output
    paths = export_spd_results(*args)
    book = load_workbook(next(p for p in paths if p.name == "updated_refdes.xlsx"))
    try:
        assert book["RefDes"]["A35"].value == "=DEMO_DUT" and book["RefDes"]["A35"].data_type == "s"
        assert list(book["RefDes"].values)[-3] == ("ALIGN_MARK", "M1", "Enabled", None)
        assert book["Cap Distance"].max_row == len(records) + 1
    finally:
        book.close()
    with board.open("ab") as stream:
        stream.write(b"* modified\n")
    with pytest.raises(ValueError, match="변경"):
        export_spd_results(*args)
    assert list(output.iterdir()) == [paths[0].parent]
