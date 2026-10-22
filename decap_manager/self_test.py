"""Small frozen-build integration check using bundled synthetic inputs only."""
from pathlib import Path
import tempfile
from zipfile import ZipFile

from openpyxl import load_workbook

from . import __version__
from .engine import compute
from .excel_io import export_results, inspect_workbook, join_distances, read_commands, read_distances, read_refdes


def self_test():
    from .gui import App, asset_root, fingerprint
    directory = asset_root() / "examples"
    paths = {kind: directory / f"{kind}.xlsx" for kind in ("refdes", "distance", "commands")}
    mappings = {kind: inspect_workbook(path, kind)[0] for kind, path in paths.items()}
    records = join_distances(read_refdes(paths["refdes"], mappings["refdes"]), read_distances(paths["distance"], mappings["distance"]))
    commands = read_commands(paths["commands"], mappings["commands"])
    result = compute(records, commands)
    assert result["ok"], result["issues"]
    assert result["changes"], "Synthetic commands must cause changes"
    assert all(s["expected_active"] == s["after_active"] for s in result["summary"])
    source_hash = fingerprint(paths["refdes"])
    with tempfile.TemporaryDirectory(prefix="decap-selftest-") as temporary:
        outputs = export_results(paths["refdes"], mappings["refdes"], result, temporary)
        updated = next(path for path in outputs if path.name == "updated_refdes.xlsx")
        reread = read_refdes(updated, mappings["refdes"])
        assert [(r["refdes"], r["net"], r["component"], r["status"]) for r in reread] == [
            (r["refdes"], r["net"], r["component"], r["status"]) for r in result["records"]]
        with ZipFile(paths["refdes"]) as original, ZipFile(updated) as final:
            changed_members = [name for name in original.namelist() if original.read(name) != final.read(name)]
            assert len(changed_members) == 1 and changed_members[0].startswith("xl/worksheets/")
        for path in outputs:
            if path.suffix == ".xlsx":
                workbook = load_workbook(path, read_only=True)
                workbook.close()
    assert source_hash == fingerprint(paths["refdes"])
    app = App()
    try:
        app.withdraw()
        app.paths = {k: str(v) for k, v in paths.items()}
        app.mappings = mappings
        app.mode.set("excel")
        app.reload_records()
        app.preview()
        assert app.result and app.result["ok"]
        assert len(app.change_table.get_children()) == len(result["changes"])
        assert len(app.summary_table.get_children()) == len(result["summary"])
        app.invalidate()
        assert app.result is None and "disabled" in app.save_button.state()
        app.update()
    finally:
        app.destroy()
    return {"ok": True, "version": __version__, "rows": len(records), "commands": len(commands),
            "changed_refdes": len(result["changes"]), "checks": ["read", "calculate", "counts", "export", "reopen", "source-preservation", "gui-preview", "preview-invalidation"]}
