"""Create fictional examples; no production workbook is used or embedded."""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def save(path, title, rows, header_row=1):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    for cell in sheet[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="174A64")
    sheet.freeze_panes = f"A{header_row + 1}"
    for column in ("A", "B", "C", "D", "E"):
        sheet.column_dimensions[column].width = 28
    workbook.save(path)
    workbook.close()


def generate(directory=None):
    directory = Path(directory or Path(__file__).parent)
    directory.mkdir(parents=True, exist_ok=True)
    components = ["CAP_0603M_10NF", "CAP_0603M_100NF", "CAP_0603M_1UF", "CAP_0603M_4U7F"]
    nodes, connections, references = [], [], []
    index = 1
    for site in (0, 1):
        net = f"U{site}_DEMO_VDD"
        x0 = site * 12000
        layer = "TOP" if site == 0 else "BOTTOM"
        # Reference: a 3 x 3 pad array at 0.8 mm pitch; the centre pad is the supply pin.
        pads = []
        for row in (-1, 0, 1):
            for column in (-1, 0, 1):
                pin, pad_net = ("P", net) if row == column == 0 else (f"G{len(pads) + 1}", "DGND")
                nodes.append(f"NodeD{site}{pin}!!{pin}::{pad_net} X = {x0 + column * 800}um Y = {row * 800}um Layer = Signal$TOP")
                pads.append(f"{pin} $Package.NodeD{site}{pin}!!{pin}::{pad_net}")
        references.append(f".Connect SITE{site} DEMO_DUT\n" + "\n".join(pads) + "\n.EndC")
        # 0603M bodies (0.6 x 0.3 mm) on a 0.7 x 1.0 mm grid beside the reference: one row
        # per part type, each slot farther from the supply pin than the previous one.
        for part, component in enumerate(components):
            for slot in range(4):
                ref = f"C{index}"
                status = "Automatic" if slot < 2 else "Disabled"
                x, y = x0 + 1900 + slot * 700, -(600 + part * 1000)
                nodes += [f"Node{index}P!!1::{net} X = {x}um Y = {y}um Layer = Signal${layer}",
                          f"Node{index}G!!2::DGND X = {x}um Y = {y - 400}um Layer = Signal${layer}"]
                usage = " Usage = 0b111000" if status == "Disabled" else ""
                connections.append(f".Connect {ref} {component}{usage}\n1 $Package.Node{index}P!!1::{net}\n2 $Package.Node{index}G!!2::DGND\n.EndC")
                index += 1
    (directory / "board.spd").write_text("\n".join([
        "* Synthetic distance and RefDes example",
        *[f".Part {name} Outline = 600um 300um\n.EndPart" for name in components],
        *[f".PartialCkt {name} ExtNode = 1 2\n* Size : 0603M\n* Capacitance : {value}[nF]\n.EndPartialCkt" for name, value in zip(components, (10, 100, 1000, 4700))],
        ".Package $Package", *nodes, ".EndPackage",
        *connections, ".Connect M1 ALIGN_MARK Usage = 0b1000\n.EndC", *references, ""
    ]), encoding="utf-8")
    save(directory / "commands.xlsx", "Adjustments", [
        ["Synthetic commands; only named NET/component pairs change"],
        ["Channel / [mΩ]", "SITE", *components],
        ["DEMO_VDD", 0, "A+1", "D-1", "D+1", None],
        ["DEMO_VDD", 1, None, "A-1,D+1", None, "D+1"],
    ], header_row=2)
    save(directory / "commands_tall.xlsx", "Commands", [
        ["NET", "Component", "Command"],
        ["U0_DEMO_VDD", "CAP_0603M_100NF", "D-1"],
        ["U1_DEMO_VDD", "CAP_0603M_4U7F", "D+1"],
    ])
    assert all((directory / filename).is_file() for filename in (
        "commands.xlsx", "commands_tall.xlsx", "board.spd"))


if __name__ == "__main__":
    generate()
