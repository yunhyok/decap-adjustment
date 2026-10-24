from copy import deepcopy
import unittest

from decap_manager.engine import compute, parse_expression
from decap_manager.shortage import find_shortages, shortage_choices


MID = "CAP_100NF"
LARGE = "CAP_1UF"
XL = "CAP_4U7F"


def record(number, component=MID, status="Automatic", net="U1_SYNTH"):
    return dict(row=number + 1, refdes=f"C{number}", net=net, component=component,
                status=status, distance=float(number))


def command(component=XL, expression="D+2", net="U1_SYNTH", source="Synthetic!E2"):
    return dict(net=net, component=component, expression=expression, source=source)


def rules():
    return [dict(target=target, donor=donor, direction="D", donor_status="any", compensate=True)
            for target, donor in ((XL, LARGE), (LARGE, MID))]


def case_24(net="U1_SYNTH", offset=0):
    rows = [record(offset + i + 1, component, net=net)
            for i, component in enumerate([MID] * 16 + [LARGE] * 6 + [XL] * 2)]
    rows[-1]["status"] = "Disabled"
    return rows


class ShortageTests(unittest.TestCase):
    def choices(self, records, commands, policies=None, aliases=None):
        shortages = find_shortages(records, commands, aliases)
        self.assertTrue(shortages)
        options = shortage_choices(records, commands, shortages[0],
                                   policies=rules() if policies is None else policies, aliases=aliases)
        for option in options:
            result = compute(records, [c for c in option["commands"] if c["net"] == shortages[0]["net"]],
                             policies=rules() if policies is None else policies, aliases=aliases)
            self.assertTrue(result["ok"], result["issues"])
            self.assertEqual(option["summary"], [s for s in result["summary"] if s["net"] == shortages[0]["net"]])
            self.assertTrue(all(s["expected_active"] == s["after_active"] for s in option["summary"]))
            self.assertEqual(option["adjustment"]["code"], "quantity_adjustment")
        return options

    def test_24_slots_23_active_offers_three_successful_choices(self):
        rows, commands = case_24(), [command()]
        shortage = find_shortages(rows, commands)[0]
        self.assertEqual({k: shortage[k] for k in ("total", "active", "requested_active", "deficit")},
                         dict(total=24, active=23, requested_active=25, deficit=1))
        self.assertEqual([(c["component"], c["total"], c["active"], c["requested_active"]) for c in shortage["components"]],
                         [(MID, 16, 16, 16), (LARGE, 6, 6, 6), (XL, 2, 1, 3)])
        options = self.choices(rows, commands)
        self.assertEqual(len(options), 3)
        self.assertEqual([o["adjustment"]["strategy"] for o in options], ["reduce_addition", "reduce_other", "reduce_other"])
        self.assertEqual(options[0]["commands"][0]["expression"], "D+1")
        for option in options[1:]:
            self.assertEqual(option["commands"][0]["expression"], "A-1")
            self.assertEqual(option["commands"][0]["source"], "수량조정팝업")
            self.assertEqual(option["commands"][1], commands[0])
            self.assertIn("가까운", option["label"])
            self.assertIn("가까운", option["description"])
        self.assertEqual([o["adjustment"]["requested_active_after"] for o in options], [24, 24, 24])

    def test_zero_addition_removed_instead_of_invalid_zero_operation(self):
        rows, commands = [record(1)], [command(LARGE, "D+1")]
        options = self.choices(rows, commands)
        self.assertEqual(options[0]["commands"], [])
        self.assertEqual(options[0]["adjustment"]["original_expression"], "D+1")
        self.assertEqual(options[0]["adjustment"]["adjusted_expression"], "")
        self.assertIn("명령 삭제", options[0]["description"])
        self.assertEqual(options[0]["summary"][0]["after_active"], 1)

    def test_compound_operations_preserve_order_and_remove_only_zero_operation(self):
        rows = [record(1), record(2, status="Disabled")]
        commands = [command(MID, "A+1,D-1,A+2")]
        options = self.choices(rows, commands)
        self.assertEqual([o["commands"][0]["expression"] for o in options], ["D-1,A+2", "A+1,D-1,A+1"])
        self.assertTrue(all("[이번계산조정]" in o["commands"][0]["source"] for o in options))
        self.assertTrue(all(o["adjustment"]["original_expression"] == commands[0]["expression"] for o in options))

    def test_alias_existing_command_merges_without_duplicate_or_reordering(self):
        rows = case_24()
        commands = [command("OLD_MID", "D-1", source="Synthetic!C2"), command(expression="D+3")]
        aliases = {"OLD_MID": MID}
        options = self.choices(rows, commands, aliases=aliases)
        option = next(o for o in options if o["adjustment"]["strategy"] == "reduce_other" and o["adjustment"]["component"] == MID)
        self.assertEqual(len(option["commands"]), 2)
        self.assertEqual(option["commands"][0]["component"], "OLD_MID")
        self.assertEqual(option["commands"][0]["expression"], "A-1,D-1")
        self.assertEqual(option["commands"][0]["source"], "Synthetic!C2 [이번계산조정]")
        self.assertEqual(option["commands"][1], commands[1])

    def test_alias_on_positive_component_uses_exact_component_counts(self):
        rows, commands = case_24(), [command("OLD_XL")]
        aliases = {"OLD_XL": XL}
        shortage = find_shortages(rows, commands, aliases)[0]
        self.assertEqual(shortage["deficit"], 1)
        self.assertNotIn("OLD_XL", {c["component"] for c in shortage["components"]})
        options = self.choices(rows, commands, aliases=aliases)
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]["commands"][0]["component"], "OLD_XL")

    def test_inputs_and_other_net_commands_are_deeply_immutable(self):
        rows = case_24() + [record(100, status="Disabled", net="OTHER")]
        other = command(MID, "A+1", net="OTHER")
        other["metadata"] = {"notes": ["unchanged"]}
        commands = [other, command()]
        original_rows, original_commands = deepcopy(rows), deepcopy(commands)
        options = self.choices(rows, commands)
        self.assertEqual(rows, original_rows)
        self.assertEqual(commands, original_commands)
        for option in options:
            self.assertEqual(option["commands"][0], other)
            self.assertTrue(all(s["net"] == "U1_SYNTH" for s in option["summary"]))
        options[0]["commands"][0]["metadata"]["notes"].append("only this choice")
        self.assertEqual(commands, original_commands)
        self.assertEqual(options[1]["commands"][0], other)

    def test_invalid_donor_distance_excludes_conversion_choices(self):
        rows, commands = case_24(), [command()]
        rows[21]["distance"] = None
        options = self.choices(rows, commands)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["adjustment"]["strategy"], "reduce_addition")

    def test_missing_chain_excludes_other_reduction_choices(self):
        options = self.choices(case_24(), [command()], policies=[])
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["commands"][0]["expression"], "D+1")

    def test_existing_reducer_is_not_silently_moved_before_failing_addition(self):
        commands = [command(expression="D+3"), command(MID, "D-1")]
        self.assertEqual(self.choices(case_24(), commands), [])

    def test_multiple_shortages_follow_command_net_order_and_exclude_enabled(self):
        rows = case_24("A") + case_24("B", offset=100) + [record(500, "UNRELATED", status="Enabled", net="B")]
        commands = [command(net="B"), command(net="A")]
        shortages = find_shortages(rows, commands)
        self.assertEqual([s["net"] for s in shortages], ["B", "A"])
        self.assertEqual([s["deficit"] for s in shortages], [1, 1])
        self.assertEqual([s["total"] for s in shortages], [24, 24])
        options = self.choices(rows, commands)
        self.assertEqual(len(options), 3)
        self.assertTrue(all(c == commands[1] for o in options for c in o["commands"] if c["net"] == "A"))
        self.assertFalse(compute(rows, options[0]["commands"], policies=rules())["ok"])

    def test_deficit_larger_than_single_positive_operation_does_not_invent_split(self):
        rows, commands = [record(1)], [command(MID, "A+1,A+1")]
        self.assertEqual(find_shortages(rows, commands)[0]["deficit"], 2)
        self.assertEqual(self.choices(rows, commands), [])

    def test_negative_requested_component_is_never_offered_as_reducer(self):
        rows = [record(1), record(2, LARGE, "Disabled"), record(3, XL)]
        commands = [command(MID, "A-1"), command(XL, "D+3")]
        options = self.choices(rows, commands)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["adjustment"]["strategy"], "reduce_addition")

    def test_no_physical_excess_is_not_reported_as_shortage(self):
        rows = case_24()
        self.assertEqual(find_shortages(rows, [command(expression="D+1")]), [])
        self.assertEqual(find_shortages(rows, [command(MID, "A-100")]), [])
        self.assertEqual(find_shortages(rows, []), [])
        with self.assertRaises(ValueError):
            find_shortages(rows, [command(expression="not a command")])

    def test_equal_compound_alternatives_are_not_duplicated(self):
        rows = [record(1), record(2, status="Disabled")]
        options = self.choices(rows, [command(MID, "A+1,A+1")])
        self.assertEqual(len(options), 1)
        self.assertEqual(parse_expression(options[0]["commands"][0]["expression"]), [("A", 1)])


if __name__ == "__main__":
    unittest.main()
