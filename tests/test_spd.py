"""Original synthetic boards; no customer layout data is stored here."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from decap_manager.spd import read_spd


def board(parts, extra="", ground=""):
    packages = {}
    connections = []
    index = 0
    for ref, part, usage, pins in parts:
        connections.append(f".Connect {ref} {part}{usage}\n")
        for pin, net, x, y, package in pins:
            index += 1
            suffix = f"::{net}" if net else ""
            packages.setdefault(package, []).append(f"Node{index}!!{pin}{suffix} X = {x} Y = {y}\n")
            connections.append(f"{pin} {package}.Node{index}!!{pin}{suffix}\n")
        connections.append(".EndC\n")
    return ("".join(f".Package {name}\n{''.join(nodes)}.EndPackage\n" for name, nodes in packages.items())
            + extra + "".join(connections) + ground)


def pin(name, net, x, y="0mm", package="$Package"):
    return name, net, x, y, package


def basic(cap_pins=None):
    return board([
        ("U1", "SOCKET", "", [pin("A", "RAIL", "0mm"), pin("B", "RAIL", "1cm"), pin("G", "DGND", "0mm")]),
        ("C1", "CAP", " Usage = 0b111000", cap_pins or [pin("1", "RAIL", "9000um"), pin("2", "DGND", "2mm")]),
        ("R1", "RESISTOR", " Usage = 0b1000", [pin("1", "", "0mm")]),
        ("MH1", "MECHANICAL", " Usage = strange", []),
    ])


class SpdTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "board.spd"

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return read_spd(self.path)

    def test_inventory_source_hash_and_nearest_same_net_pin(self):
        layout = self.load(basic())
        self.assertEqual(layout.components, {"SOCKET": 1, "CAP": 1, "RESISTOR": 1, "MECHANICAL": 1})
        self.assertEqual([r["row"] for r in layout.inventory], [2, 3, 4, 5])
        self.assertEqual([r["status"] for r in layout.inventory], ["Automatic", "Disabled", "Enabled", "Unknown"])
        self.assertEqual([r["net"] for r in layout.inventory], ["RAIL", "RAIL", "", ""])
        self.assertEqual(layout.source_hash, hashlib.sha256(self.path.read_bytes()).hexdigest())
        rows = layout.records("SOCKET", {"CAP"})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["row"], rows[0]["distance"], rows[0]["net"]), (3, 1000.0, "RAIL"))
        rows[0]["component"] = "OTHER"
        self.assertEqual(layout.inventory[1]["component"], "CAP")

    def test_ground_first_inventory_is_preserved_and_signal_selected(self):
        layout = self.load(basic([pin("2", "DGND", "0mm"), pin("1", "RAIL", "9mm")]))
        self.assertEqual(layout.inventory[1]["net"], "DGND")
        self.assertEqual(layout.records("SOCKET", ["CAP"])[0]["net"], "RAIL")
        self.assertEqual(layout.inventory[1]["net"], "DGND")

    def test_netlist_defines_unusual_return_name(self):
        text = basic().replace("DGND", "RETURN_PLANE")
        with self.assertRaisesRegex(ValueError, "신호 NET이 모호"):
            self.load(text).records("SOCKET", {"CAP"})
        layout = self.load(text + ".NetList\nRETURN_PLANE -> GroundNets\n.EndNetList\n")
        self.assertEqual(layout.records("SOCKET", {"CAP"})[0]["net"], "RAIL")

    def test_netlist_does_not_inherit_groups_or_flatten_arrow_chains(self):
        text = basic().replace("DGND", "ODD_RETURN")
        for declaration in ("OTHER_RETURN -> GroundNets\nODD_RETURN Color = RED\n",
                            "ODD_RETURN -> RETURN_GROUP\nRETURN_GROUP -> GroundNets\n"):
            with self.subTest(declaration=declaration), self.assertRaisesRegex(ValueError, "NET 후보: ODD_RETURN, RAIL"):
                self.load(text + f".NetList\n{declaration}.EndNetList\n").records("SOCKET", {"CAP"})

    def test_missing_reference_error_names_target_net_and_reference(self):
        with self.assertRaisesRegex(ValueError, "RefDes C1: 기준 RefDes U1에 대상 NET ABSENT"):
            self.load(basic([pin("1", "ABSENT", "0mm")])).records("SOCKET", {"CAP"})

    def test_multiple_signal_nets_and_missing_signal_reference_fail(self):
        for pins, message in [([pin("1", "RAIL", "0mm"), pin("2", "OTHER", "1mm")], "신호 NET이 모호"),
                              ([pin("1", "UNOWNED", "0mm"), pin("2", "DGND", "0mm")], "에 대상 NET")]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.load(basic(pins)).records("SOCKET", {"CAP"})

    def test_multisite_uses_exclusive_net_not_global_nearest_site(self):
        layout = self.load(board([
            ("U1", "DUT", "", [pin("P", "P1", "0mm"), pin("G", "GND", "0mm")]),
            ("U2", "DUT", "", [pin("P", "P2", "100mm"), pin("G", "GND", "100mm")]),
            ("C1", "CAP", "", [pin("P", "P1", "99mm"), pin("G", "GND", "100mm")]),
            ("C2", "CAP", "", [pin("P", "P2", "101mm"), pin("G", "GND", "0mm")]),
        ]))
        rows = layout.records("DUT", {"CAP"})
        self.assertEqual([r["distance"] for r in rows], [99000, 1000])

    def test_shared_net_only_and_conflicting_exclusive_sites_fail(self):
        for pins in ([pin("G", "GND", "0mm")], [pin("1", "P1", "0mm"), pin("2", "P2", "0mm")]):
            text = board([
                ("U1", "DUT", "", [pin("P", "P1", "0mm"), pin("G", "GND", "0mm")]),
                ("U2", "DUT", "", [pin("P", "P2", "1mm"), pin("G", "GND", "1mm")]),
                ("C1", "CAP", "", pins),
            ])
            with self.subTest(pins=pins), self.assertRaisesRegex(ValueError, "기준 사이트가 모호"):
                self.load(text).records("DUT", {"CAP"})

    def test_all_target_pins_are_considered(self):
        rows = self.load(basic([pin("1", "RAIL", "4mm"), pin("2", "RAIL", "9.75mm")])).records("SOCKET", {"CAP"})
        self.assertEqual(rows[0]["distance"], 250)

    def test_supported_units_and_package_scoped_identity(self):
        for length in ("25400um", "25400µm", "25400μm", "25.4 mm", "2.54cm", ".0254m", "1000mil", "1in", "1e0inch"):
            with self.subTest(length=length):
                text = board([("U", "DUT", "", [pin("1", "P", "0mm")]),
                              ("C", "CAP", "", [pin("1", "P", length, package="$Other")])])
                self.assertAlmostEqual(self.load(text).records("DUT", {"CAP"})[0]["distance"], 25400)
        text = basic().replace("$Package.Node4", "$Other.Node4")
        text = ".Package $Other\nNode4!!1::RAIL X = 8mm Y = 0mm\n.EndPackage\n" + text
        self.assertEqual(self.load(text).records("SOCKET", {"CAP"})[0]["distance"], 2000)

    def test_optional_connection_net_is_resolved_from_node(self):
        text = basic().replace("1 $Package.Node4!!1::RAIL", "1 $Package.Node4!!1")
        layout = self.load(text)
        self.assertEqual(layout.inventory[1]["net"], "DGND")
        self.assertEqual(layout.records("SOCKET", {"CAP"})[0]["net"], "RAIL")

    def test_unmapped_invalid_geometry_is_not_parsed(self):
        text = basic().replace(".EndPackage", "Node999::RAIL X = invalid Y = invalid\n.EndPackage", 1)
        self.assertEqual(self.load(text).records("SOCKET", {"CAP"})[0]["distance"], 1000)

    def test_connected_missing_nodes_and_conflicts_fail(self):
        for text in (basic().replace("$Package.Node4!!1", "$Package.Node404!!1"),
                     basic().replace("1 $Package.Node4!!1::RAIL", "1 $Package.Node4!!1::OTHER"),
                     basic().replace("$Package.Node4!!1", "$Package.Node4!!9"),
                     basic().replace(".EndPackage", "Node4!!1::RAIL X = 4mm Y = 0mm\n.EndPackage", 1)):
            with self.subTest(text=text[-50:]), self.assertRaises(ValueError):
                self.load(text)

    def test_bad_mapped_geometry_rejected(self):
        for value in ("3", "3parsecs", "NaNmm", "1e999mm", "2mm/in", "1mm X = 2mm"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(basic().replace("9000um", value))

    def test_duplicate_refdes_pins_and_unterminated_blocks_rejected(self):
        for text in (basic() + ".Connect C1 CAP\n.EndC\n", basic() + ".Connect NEW CAP\n",
                     basic().replace(".EndPackage", "", 1),
                     basic().replace("1 $Package.Node4!!1::RAIL", "1 $Package.Node4!!1::RAIL\n1 $Package.Node4!!1::RAIL"),
                     basic() + ".NetList\n", basic() + ".EndC\n"):
            with self.subTest(text=text[-50:]), self.assertRaises(ValueError):
                self.load(text)

    def test_zero_connected_targets_fail_and_inventory_is_retained(self):
        layout = self.load(basic())
        for target in ("MECHANICAL", "RESISTOR"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "거리를 계산할 수 없습니다"):
                layout.records("SOCKET", {target})
        self.assertEqual(len(layout.inventory), 4)

    def test_mixed_connected_and_unconnected_targets_keep_full_inventory(self):
        text = basic().replace("R1 RESISTOR", "R1 CAP").replace("MH1 MECHANICAL", "MH1 CAP")
        layout = self.load(text)
        rows = layout.records("SOCKET", {"CAP"})
        self.assertEqual([r["refdes"] for r in rows], ["C1"])
        self.assertEqual([r["refdes"] for r in layout.inventory], ["U1", "C1", "R1", "MH1"])
        self.assertEqual(layout.components["CAP"], 3)

    def test_missing_unconnected_physical_node_is_not_excluded(self):
        text = basic().replace("R1 RESISTOR", "R1 CAP").replace("$Package.Node6!!1", "$Package.Node404!!1")
        with self.assertRaisesRegex(ValueError, "RefDes R1.*물리 노드"):
            self.load(text).records("SOCKET", {"CAP"})

    def test_selection_validation(self):
        layout = self.load(basic())
        for ref, target in (("missing", {"CAP"}), ("SOCKET", {"missing"}), ("SOCKET", set()),
                            ("SOCKET", {"SOCKET"}), ("SOCKET", "CAP")):
            with self.subTest(ref=ref, target=target), self.assertRaises(ValueError):
                layout.records(ref, target)

    def test_read_stability_checked(self):
        self.path.write_text(basic(), encoding="utf-8")
        with patch("decap_manager.spd._signature", side_effect=[(1,), (2,)]):
            with self.assertRaisesRegex(ValueError, "읽는 동안 파일이 변경"):
                read_spd(self.path)

    def test_continued_usage_is_not_mistaken_for_automatic(self):
        for attribute in ("+ Usage = 0b111000", "Usage = 0b111000", "Usage=0b111000"):
            text = basic().replace(".Connect C1 CAP Usage = 0b111000", ".Connect C1 CAP\n" + attribute)
            self.assertEqual(self.load(text).records("SOCKET", {"CAP"})[0]["status"], "Disabled")
        with self.assertRaisesRegex(ValueError, "Usage"):
            self.load(basic().replace(".Connect C1 CAP Usage = 0b111000", ".Connect C1 CAP Usage = 0b111000\n+ Usage = 0b1000"))

    def test_present_broken_usage_is_not_automatic(self):
        for usage in (" Usage =", " Usage = Checked = 1", " Usage = Checked=1", " Usage = =",
                      " Usage = strange Usage ="):
            with self.subTest(usage=usage), self.assertRaisesRegex(ValueError, "Usage"):
                self.load(basic().replace(".Connect U1 SOCKET", ".Connect U1 SOCKET" + usage))
        self.assertEqual(self.load(basic().replace(".Connect U1 SOCKET", ".Connect U1 SOCKET Usage = future_token"))
                         .inventory[0]["status"], "Unknown")

    def test_streaming_line_size_is_bounded(self):
        self.path.write_bytes(b"x" * 65)
        with patch("decap_manager.spd._MAX_LINE", 64), self.assertRaisesRegex(ValueError, "한 줄이"):
            read_spd(self.path)

    def test_bom_and_crlf(self):
        self.path.write_bytes(b"\xef\xbb\xbf" + basic().replace("\n", "\r\n").encode())
        self.assertEqual(read_spd(self.path).records("SOCKET", {"CAP"})[0]["distance"], 1000)


if __name__ == "__main__":
    unittest.main()
