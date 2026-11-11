from pathlib import Path

import pytest
from openpyxl import Workbook

from decap_manager import excel_io as io


def make_book(path, rows, title="Data"):
    book = Workbook()
    book.active.title = title
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    return path


def test_wide_commands_detect_headers_and_build_site_channel_net(tmp_path):
    path = make_book(tmp_path / "wide.xlsx", [
        ["Instructions"], [], ["Channel / [mΩ]", "SITE", "CAP_100NF", "CAP_1UF"],
        ["VDD", 2, "A-2,D+1", None], ["VDD", 3, None, "d+1"],
    ])
    mappings = io.inspect_workbook(path)
    assert mappings[0]["header_row"] == 3
    assert mappings[0]["columns"] == {"channel": 1, "site": 2}
    commands = io.read_commands(path, mappings[0])
    assert [(cmd["net"], cmd["component"], cmd["expression"]) for cmd in commands] == [
        ("U2_VDD", "CAP_100NF", "A-2,D+1"), ("U3_VDD", "CAP_1UF", "D+1")]
    assert commands[0]["source"] == "Data!C4"


def test_tall_commands_support_aliases_and_custom_net_template(tmp_path):
    path = make_book(tmp_path / "tall.xlsx", [
        ["Channel", "Site", "Component type", "Adjustment"],
        ["VDD", "04", "CUSTOM_PART", "a + 2"],
    ])
    mapping = io.inspect_workbook(path)[0]
    mapping["net_template"] = "{channel}/{site}"
    assert io.read_commands(path, mapping) == [
        {"net": "VDD/04", "component": "CUSTOM_PART", "expression": "A + 2", "source": "Data!D2"}]


def test_net_column_tall_commands_and_multiple_sheet_candidates(tmp_path):
    path = make_book(tmp_path / "multiple.xlsx", [["NET", "Part", "Command"], ["N1", "CAP", "A+1"]])
    from openpyxl import load_workbook
    book = load_workbook(path)
    sheet = book.create_sheet("Second")
    sheet.append(["NET", "Part", "Command"])
    sheet.append(["N2", "CAP", "D-1"])
    book.save(path)
    book.close()
    mappings = io.inspect_workbook(path)
    assert [mapping["sheet"] for mapping in mappings] == ["Data", "Second"]
    assert io.read_commands(path, mappings[1])[0]["net"] == "N2"


def test_mapping_rejects_unsupported_kind_and_invalid_columns(tmp_path):
    path = make_book(tmp_path / "commands.xlsx", [["NET", "Part", "Command"], ["N", "CAP", "A+1"]])
    with pytest.raises(ValueError, match="명령 파일만"):
        io.inspect_workbook(path, "distance")
    mapping = io.inspect_workbook(path)[0]
    mapping["columns"]["net"] = 1
    mapping["component_columns"] = {"CAP_100NF": 1}
    with pytest.raises(ValueError, match="중복"):
        io.read_commands(path, mapping)


@pytest.mark.parametrize("headers", [
    ["NET", "NET Name", "CAP_100NF"],
    ["Channel", "Site", "CAP_100NF", "CAP_100NF"],
])
def test_duplicate_headers_require_manual_correction(tmp_path, headers):
    path = make_book(tmp_path / "duplicate.xlsx", [headers, ["N", "N", None, None]])
    with pytest.raises(ValueError, match="중복/모호한 헤더"):
        io.inspect_workbook(path)


@pytest.mark.parametrize("row", [
    [None, "CAP_100NF", "A+1"],
    ["N", None, "A+1"],
    ["N", "CAP_100NF", "foo"],
    ["N", "CAP_100NF", 0],
    ["N", "CAP_100NF", "=1+1"],
])
def test_invalid_command_rows_are_rejected(tmp_path, row):
    path = make_book(tmp_path / "invalid.xlsx", [["NET", "Component", "Command"], row])
    with pytest.raises(ValueError):
        io.read_commands(path, io.inspect_workbook(path)[0])


def test_formulas_in_active_input_row_are_rejected(tmp_path):
    path = make_book(tmp_path / "formula.xlsx", [["NET", "Component", "Command"], ["N", "CAP", "A+1"]])
    from openpyxl import load_workbook
    book = load_workbook(path)
    book.active["A2"] = "=\"N\""
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="수식"):
        io.read_commands(path, io.inspect_workbook(path)[0])


def test_blank_command_rows_are_ignored_but_blank_commands_stay_empty(tmp_path):
    path = make_book(tmp_path / "blank.xlsx", [["NET", "Component", "Command"],
                                                [None, None, None], ["N", "CAP", None]])
    assert io.read_commands(path, io.inspect_workbook(path)[0]) == []


def test_manual_mapping_validates_required_identity_and_format(tmp_path):
    path = make_book(tmp_path / "manual.xlsx", [["NET", "Part", "Command"], ["N", "CAP", "A+1"]])
    mapping = io.inspect_workbook(path)[0]
    mapping["columns"] = {"component": 2, "expression": 3}
    with pytest.raises(ValueError, match="NET 또는 Channel/SITE"):
        io.read_commands(path, mapping)
    mapping["columns"] = {"net": 1, "component": 2}
    with pytest.raises(ValueError, match="한 가지 형식"):
        io.read_commands(path, mapping)


def test_fingerprint_tracks_file_contents(tmp_path):
    path = Path(tmp_path / "source.xlsx")
    path.write_bytes(b"original")
    before = io.fingerprint(path)
    path.write_bytes(b"changed")
    assert io.fingerprint(path) != before
