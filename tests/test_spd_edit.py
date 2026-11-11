from pathlib import Path

import pytest

from decap_manager import spd


def _board():
    text = """\t.Connect\tC1\tCAP_A Checked = 1
\t+  Usage = 0b111000   Other = keep
2 $Package.Node2!!2::GND
1 $Package.Node1!!1::VDD
.EndC
.Connect C2 CAP_B Usage = future_flag Checked = 1
1 $Package.Node3!!1::VDD
2 $Package.Node4!!2::GND
.EndC
.Connect C3 CAP_A
1 $Package.Node5!!1::VDD
2 $Package.Node6!!2::GND
.EndC
.Connect U1 DUT
A $Package.Node7!!A::VDD
.EndC
.Package $Package
Node1!!1::VDD X = 50um Y = 50um Layer = Signal$BOTTOM
Node2!!2::GND X = 150um Y = 50um Layer = Signal$BOTTOM
Node3!!1::VDD X = 300um Y = 60um Layer = Signal$TOP
Node4!!2::GND X = 350um Y = 60um Layer = Signal$TOP
Node5!!1::VDD X = 450um Y = 80um Layer = Signal$BOTTOM
Node6!!2::GND X = 500um Y = 80um Layer = Signal$L02
Node7!!A::VDD X = 0mm Y = 0mm Layer = Signal$TOP
.EndPackage
.Part CAP_A W3DCktWidth = 0mm
+ Outline = 0.2mm 550um
.Part CAP_B W3DCktWidth = 0mm
+ Outline = 0.2mm 550um
.PartialCkt CAP_A ExtNode = 1
+ 2
* Description :1005M(0402)/X5R(-55to85[deg])/1.00e-01[uF]/100[V]
* Property : C = 100000[pF]
C01 1 2 9.30e-08
.EndPartialCkt
.PartialCkt CAP_B ExtNode = 1 2
* Package Size: 1005M
* Nominal Capacitance = 2.2 µF
* Property : C = 2200[nF]
C01 1 2 2.18e-06
.EndPartialCkt
* Capacitance: 77 pF
* vendor bytes are kept exactly: """
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode() + b"\x81"


@pytest.fixture
def board(tmp_path):
    source = tmp_path / "source.spd"
    source.write_bytes(_board())
    layout = spd.read_spd(source)
    rows = {row["refdes"]: row for row in layout.records("DUT", {"CAP_A", "CAP_B"})}
    return source, layout, rows, tmp_path / "output.spd"


def test_geometry_and_metadata_use_physical_pins_and_model_comments(board):
    _, layout, rows, _ = board
    geometry = layout.geometry()
    assert geometry["C1"] == dict(x=100, y=50, side="Bottom", pins=[(150, 50), (50, 50)], size=(200, 550), angle=90)
    assert geometry["C2"]["side"] == geometry["U1"]["side"] == "Top"
    assert geometry["C3"]["side"] == "Unknown"
    assert layout.scope_metadata("DUT", {"CAP_A", "CAP_B"}) == {ref: dict(site="U1", channel="VDD") for ref in rows}
    assert rows["C1"]["distance"] == pytest.approx(70.710678)
    assert layout.inventory[0]["net"] == "GND" and rows["C1"]["net"] == "VDD"
    assert layout.component_metadata["CAP_A"] == dict(size="1005M", capacitance_pf=100000, source="SPICE comment")
    assert layout.component_metadata["CAP_B"] == dict(size="1005M", capacitance_pf=2200000, source="SPICE comment")
    assert layout.component_metadata["DUT"] == dict(size=None, capacitance_pf=None, source=None)


def test_site_prefix_is_removed_from_display_channel_only():
    layout = spd.read_spd(Path(__file__).resolve().parents[1] / "examples" / "board.spd")
    target = {"CAP_0603M_10NF"}
    assert layout.scope_metadata("DEMO_DUT", target)["C1"] == dict(site="SITE0", channel="DEMO_VDD")
    assert layout.records("DEMO_DUT", target)[0]["net"] == "U0_DEMO_VDD"


def test_save_rewrites_only_selected_fields_and_reopens(board):
    source, layout, rows, output = board
    assert spd.save_spd(source, layout, [dict(rows["C1"], component="CAP_B", status="Automatic"),
                                         dict(rows["C3"], status="Disabled")], output) == output
    expected = (_board().replace(b"\tC1\tCAP_A", b"\tC1\tCAP_B")
                .replace(b"\t+  Usage = 0b111000   Other = keep", b"\t+     Other = keep")
                .replace(b".Connect C3 CAP_A\r\n", b".Connect C3 CAP_A Usage = 0b111000\r\n"))
    assert source.read_bytes() == _board()
    assert output.read_bytes() == expected
    reread = spd.read_spd(output)
    assert [(row["component"], row["status"]) for row in reread.inventory] == [
        ("CAP_B", "Automatic"), ("CAP_B", "Unknown"), ("CAP_A", "Disabled"), ("DUT", "Automatic")]


def test_usage_replacement_handles_known_and_future_tokens(board):
    source, layout, rows, output = board
    spd.save_spd(source, layout, [dict(rows["C1"], status="Enabled"), dict(rows["C2"], status="Disabled")], output)
    assert output.read_bytes() == (_board().replace(b"Usage = 0b111000", b"Usage = 0b1000")
                                    .replace(b"Usage = future_flag", b"Usage = 0b111000"))


def test_removing_standalone_usage_preserves_the_line_ending(board):
    source, _, _, output = board
    raw = _board().replace(b"   Other = keep", b"")
    source.write_bytes(raw)
    layout = spd.read_spd(source)
    first, *_ = layout.records("DUT", {"CAP_A"})
    spd.save_spd(source, layout, [dict(first, status="Automatic")], output)
    assert output.read_bytes() == raw.replace(b"\t+  Usage = 0b111000\r\n", b"\t  \r\n")


def test_usage_edits_preserve_quoted_values_and_vendor_attributes(board):
    source, _, _, output = board
    attrs = rb""" Other="quoted \" Usage = 0b1000" Vendor-Usage=0b111000"""
    c1 = b"\t.Connect\tC1\tCAP_A Checked = 1"
    c3 = b".Connect C3 CAP_A"
    raw = (_board().replace(c1, c1 + attrs + b"\r\nVendor-Flag = keep")
           .replace(c3, c3 + b" Other='Usage = 0b111000'"))
    source.write_bytes(raw)
    layout = spd.read_spd(source)
    first, third = layout.records("DUT", {"CAP_A"})
    assert (first["status"], third["status"]) == ("Disabled", "Automatic")
    spd.save_spd(source, layout, [dict(first, status="Automatic"), dict(third, status="Disabled")], output)
    expected = (raw.replace(b"\t+  Usage = 0b111000", b"\t+  ")
                .replace(b"Other='Usage = 0b111000'\r\n", b"Other='Usage = 0b111000' Usage = 0b111000\r\n"))
    assert output.read_bytes() == expected


@pytest.mark.parametrize("attrs", [b'Other="Usage = 0b111000', b"Other = Usage = 0b111000"])
def test_ambiguous_usage_is_rejected_before_editing(board, attrs):
    source, _, _, _ = board
    source.write_bytes(_board().replace(b".Connect C3 CAP_A", b".Connect C3 CAP_A " + attrs))
    with pytest.raises(ValueError, match="따옴표|Usage"):
        spd.read_spd(source)


@pytest.mark.parametrize("changes", [{"status": "Unknown"}, {"status": "other"}, {"net": "GND"}, {"row": 3},
                                      {"refdes": "missing"}, {"component": "missing"}])
def test_save_refuses_unsupported_changes(board, changes):
    source, layout, rows, output = board
    with pytest.raises(ValueError, match="SPD"):
        spd.save_spd(source, layout, [dict(rows["C1"], **changes)], output)
    assert not output.exists()


@pytest.mark.parametrize("old,new", [(b"Outline = 0.2mm", b"Outline = 0.3mm"), (b"ExtNode = 1 2", b"ExtNode = 1 3")])
def test_conversion_rejects_incompatible_part_geometry_or_model_pins(board, old, new):
    source, _, _, output = board
    source.write_bytes(_board().replace(old, new, 1))
    layout = spd.read_spd(source)
    first, *_ = layout.records("DUT", {"CAP_A"})
    edits = [dict(first, component="CAP_B")]
    with pytest.raises(ValueError, match="호환되지 않습니다"):
        layout.validate_records(edits)
    with pytest.raises(ValueError, match="호환되지 않습니다"):
        spd.save_spd(source, layout, edits, output)
    assert not output.exists()


def test_conversion_without_models_uses_existing_compatible_pin_map(board):
    source, _, _, output = board
    source.write_bytes(_board().split(b".PartialCkt", 1)[0])
    layout = spd.read_spd(source)
    first, *_ = layout.records("DUT", {"CAP_A"})
    spd.save_spd(source, layout, [dict(first, component="CAP_B")], output)
    assert spd.read_spd(output).inventory[0]["component"] == "CAP_B"


def test_conversion_reads_extnode_label_and_pin_list_on_continuations(board):
    source, _, _, output = board
    source.write_bytes(_board().replace(b".PartialCkt CAP_B ExtNode = 1 2",
                                        b".PartialCkt CAP_B\r\n+ ExtNode = 1\r\n+ 2"))
    layout = spd.read_spd(source)
    first, *_ = layout.records("DUT", {"CAP_A"})
    spd.save_spd(source, layout, [dict(first, component="CAP_B")], output)
    assert spd.read_spd(output).inventory[0]["component"] == "CAP_B"


def test_stale_source_before_and_during_copy_leaves_no_output(board, monkeypatch):
    source, layout, rows, output = board
    changed = _board().replace(b"Other = keep", b"Other = copy")
    source.write_bytes(changed)
    edits = [dict(rows["C1"], status="Automatic")]
    with pytest.raises(ValueError, match="변경"):
        spd.save_spd(source, layout, edits, output)
    source.write_bytes(_board())
    lines = spd._lines

    def change_during_copy(path, digest=None, *, original=False):
        if original:
            path.write_bytes(changed)
        return lines(path, digest, original=original)

    monkeypatch.setattr(spd, "_lines", change_during_copy)
    with pytest.raises(ValueError, match="변경"):
        spd.save_spd(source, layout, edits, output)
    assert list(output.parent.iterdir()) == [source]


def test_failed_reopen_and_destination_race_are_atomic(board, monkeypatch):
    source, layout, rows, output = board
    edits = [dict(rows["C1"], status="Automatic")]
    real_read = spd.read_spd

    def fail(_):
        raise ValueError("검증 실패")

    monkeypatch.setattr(spd, "read_spd", fail)
    with pytest.raises(ValueError, match="검증 실패"):
        spd.save_spd(source, layout, edits, output)
    assert list(output.parent.iterdir()) == [source]

    def create_destination(path):
        output.write_bytes(b"already here")
        return real_read(path)

    monkeypatch.setattr(spd, "read_spd", create_destination)
    with pytest.raises(FileExistsError):
        spd.save_spd(source, layout, edits, output)
    assert output.read_bytes() == b"already here"
    with pytest.raises(FileExistsError):
        spd.save_spd(source, layout, edits, output)
    with pytest.raises(ValueError, match="원본 파일에는 저장할 수 없습니다"):
        spd.save_spd(source, layout, edits, source)
    assert set(output.parent.iterdir()) == {source, output}
    assert source.read_bytes() == _board()


def test_conflicting_comments_and_missing_comments_are_unknown(board):
    source, _, _, _ = board
    source.write_bytes(_board().replace(b"100000[pF]", b"200000[pF]")
                       .replace(b"* Package Size: 1005M", b"* no size label"))
    metadata = spd.read_spd(source).component_metadata
    assert metadata["CAP_A"] == dict(size="1005M", capacitance_pf=None, source="SPICE comment")
    assert metadata["CAP_B"] == dict(size=None, capacitance_pf=2200000, source="SPICE comment")


def test_overflowing_optional_numbers_stay_unknown_and_mapped_coordinates_raise_value_error(board):
    source, _, _, _ = board
    raw = (_board().replace(b"Outline = 0.2mm", b"Outline = 1e1000000mm", 1)
           .replace("2.2 µF".encode(), b"1e1000000 pF").replace(b"2200[nF]", b"1e1000000[pF]"))
    source.write_bytes(raw)
    layout = spd.read_spd(source)
    assert layout.geometry()["C1"]["size"] is None
    assert layout.component_metadata["CAP_B"]["capacitance_pf"] is None
    source.write_bytes(raw.replace(b"X = 50um", b"X = 1e1000000mm", 1))
    with pytest.raises(ValueError, match=r"SPD \d+행: 좌표"):
        spd.read_spd(source)


def test_manual_type_metadata_round_trips_as_inert_comments(board):
    source, layout, _, output = board
    override = dict(size="크기 1005M", capacitance_pf=1234.5)
    spd.save_spd(source, layout, [], output, component_metadata={"CAP_A": override})
    assert output.read_bytes().startswith(_board() + b"\r\n* DecapAdjustment TypeMetadata ")
    reopened = spd.read_spd(output)
    assert reopened.component_metadata["CAP_A"] == dict(override, source="Manual")
    assert reopened.inventory == layout.inventory

    again = output.with_name("again.spd")
    override["capacitance_pf"] = 10
    spd.save_spd(output, reopened, [], again, component_metadata={"CAP_A": override})
    assert again.read_bytes().count(b"* DecapAdjustment TypeMetadata ") == 1
    assert spd.read_spd(again).component_metadata["CAP_A"] == dict(override, source="Manual")
    with pytest.raises(ValueError, match="표시 가능한"):
        spd.save_spd(source, layout, [], again.with_name("invalid.spd"),
                     component_metadata={"CAP_A": dict(size="bad\n.End", capacitance_pf=1)})
