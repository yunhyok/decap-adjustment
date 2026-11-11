"""Frozen-build integration check using bundled synthetic SPD inputs only."""
from pathlib import Path
import tempfile

from . import __version__
from .engine import compute
from .excel_io import fingerprint, inspect_workbook, read_commands
from .spd import read_spd, save_spd


def self_test():
    from .gui import App, asset_root
    from .help_viewer import show_help
    directory = asset_root() / "examples"
    board = directory / "board.spd"
    layout = read_spd(board)
    targets = [name for name in layout.components if name.startswith("CAP_")]
    records = layout.records("DEMO_DUT", targets)
    commands_path = directory / "commands.xlsx"
    commands = read_commands(commands_path, inspect_workbook(commands_path, "commands")[0])
    result = compute(records, commands)
    assert result["ok"] and len(result["changes"]) == 6, result["issues"]
    assert all(s["expected_active"] == s["after_active"] for s in result["summary"])
    assert {v["side"] for v in layout.geometry().values()} >= {"Top", "Bottom"}
    assert layout.component_metadata[targets[0]]["capacitance_pf"] is not None
    with tempfile.TemporaryDirectory(prefix="decap-selftest-") as temporary:
        saved = save_spd(board, layout, result["records"], Path(temporary) / "adjusted.spd")
        assert read_spd(saved).records("DEMO_DUT", targets) == result["records"]
    assert fingerprint(board) == layout.source_hash
    app = App()
    try:
        app.withdraw()
        app.load_layout(board, layout, "DEMO_DUT", targets)
        app.manual = commands
        app.render_commands()
        app.invalidate()
        app.preview()
        assert app.result and app.result["ok"]
        assert len(app.change_table.get_children()) == 6
        app.apply_preview()
        assert app.records == result["records"] and not app.manual
        app.undo()
        assert app.records == records and app.manual == commands
        app.redo()
        assert app.records == result["records"]
        app.apply_selection({records[0]["refdes"]}, "Disabled")
        assert app.records[0]["status"] == "Disabled"
        app.help()
        assert any(child.winfo_class() == "Toplevel" for child in app.winfo_children())
        app.update()
    finally:
        app.destroy()
    return {"ok": True, "version": __version__, "rows": len(records), "commands": len(commands),
            "changed_refdes": len(result["changes"]), "checks": ["spd-read", "calculate", "counts", "spd-save", "reopen", "source-preservation", "top-bottom", "model-metadata", "gui-apply", "undo-redo", "direct-selection", "html-help"]}
