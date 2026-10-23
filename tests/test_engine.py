import copy
import random
import unittest

from decap_manager.engine import compute, parse_capacitance, parse_expression


SMALL = "CAP_0603M_10NF"
MID = "CAP_0603M_100NF"
LARGE = "CAP_0603M_1UF"
XL = "CAP_0603M_4U7F"


def row(number, component=MID, status="Disabled", distance=None, net="U1_TEST"):
    return dict(row=number + 1, refdes=f"C{number}", net=net, component=component,
                status=status, distance=number if distance is None else distance)


def command(component=MID, expression="A+1", net="U1_TEST"):
    return dict(net=net, component=component, expression=expression, source="합성 명령")


def policy(target, donor, direction="D", donor_status="Disabled", compensate=False):
    return dict(target=target, donor=donor, direction=direction,
                donor_status=donor_status, compensate=compensate)


def active(result, component, net="U1_TEST"):
    return sum(r["component"] == component and r["net"] == net and r["status"] == "Automatic"
               for r in result["records"])


class ParsingTests(unittest.TestCase):
    def test_explicit_units_not_package_numbers(self):
        for text, expected in {
            SMALL: 10_000, MID: 100_000, LARGE: 1_000_000, XL: 4_700_000,
            "CAP_1004_1UF___10": 1_000_000, "CAP_1004_1UF__10": 1_000_000,
            "4.7µF": 4_700_000, "4.7μF": 4_700_000,
            "2N2F": 2_200, "47pF": 47, "CAP_1MF": 1e9,
        }.items():
            with self.subTest(text=text):
                self.assertEqual(parse_capacitance(text), expected)
        for text in ("CAP_0603M_10", "CAP_1004___10", "CAP_1UF_10NF", "UNFAMILIAR", "0UF", "4.2U7F", "A1UF", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_capacitance(text))

    def test_expression_sequence(self):
        self.assertEqual(parse_expression(" A-2, D+1, a + 3 "), [("A", -2), ("D", 1), ("A", 3)])
        for expression in ("", "A+0", "A1", "+1", "A+1,", "A+1 D-1", "A+1.5", "B+2", None):
            with self.subTest(expression=expression):
                self.assertRaises(ValueError, parse_expression, expression)


class EngineTests(unittest.TestCase):
    def assert_atomic_error(self, result, original, code):
        self.assertFalse(result["ok"])
        self.assertEqual(result["records"], original)
        self.assertEqual(result["changes"], [])
        self.assertIn(code, {issue["code"] for issue in result["issues"]})
        self.assertTrue(all(not a["applied"] for a in result["actions"]))

    def test_eligible_status_only_and_distance_direction(self):
        records = [row(1, status="Automatic"), row(2), row(3), row(4, status="Automatic")]
        original = copy.deepcopy(records)
        result = compute(records, [command(expression="A+1,D-1")])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([r["status"] for r in result["records"]], ["Automatic", "Automatic", "Disabled", "Disabled"])
        self.assertEqual([a["refdes"] for a in result["actions"]], ["C2", "C4"])
        self.assertEqual(records, original)
        self.assertEqual(active(result, MID), 2)

    def test_opposite_comma_commands_use_current_state(self):
        records = [row(1), row(2)]
        result = compute(records, [command(expression="A+1,A-1")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["records"], records)
        self.assertEqual(result["changes"], [])
        self.assertEqual([a["refdes"] for a in result["actions"]], ["C1", "C1"])

    def test_duplicate_rows_are_conflicting_even_with_alias(self):
        records = [row(1), row(2)]
        commands = [command(), command("OLD", "D-1")]
        result = compute(records, commands, aliases={"OLD": MID})
        self.assert_atomic_error(result, records, "conflicting_commands")

    def test_unknown_nearly_matching_component_requires_alias(self):
        actual = "CAP_1004_1UF___10"
        requested = "CAP_1004_1UF__10"
        records = [row(1, actual)]
        self.assert_atomic_error(compute(records, [command(requested)]), records, "unknown_component")
        result = compute(records, [command(requested)], aliases={requested: actual})
        self.assertTrue(result["ok"])
        self.assertEqual(result["records"][0]["component"], actual)

    def test_missing_distance_in_any_eligible_candidate_is_error(self):
        records = [row(1), row(2)]
        records[1]["distance"] = None
        self.assert_atomic_error(compute(records, [command()]), records, "missing_distance")

    def test_invalid_distances_are_errors_only_when_relevant(self):
        for bad in (float("inf"), float("nan"), -1, "2", True):
            with self.subTest(bad=bad):
                records = [row(1)]
                records[0]["distance"] = bad
                self.assert_atomic_error(compute(records, [command()]), records, "missing_distance")
        records = [row(1), row(2, net="UNTOUCHED")]
        records[1]["distance"] = None
        result = compute(records, [command()])
        self.assertTrue(result["ok"])
        self.assertEqual(result["records"][1], records[1])
        self.assertIn("unavailable_distance", {i["code"] for i in result["issues"]})

    def test_untargeted_types_nets_and_extra_fields_never_change(self):
        records = [row(1), row(2, XL), row(3, net="U2_TEST"), row(4, "OTHER", "Enabled", net="")]
        records[0]["note"] = "preserve extra values"
        result = compute(records, [command()])
        self.assertTrue(result["ok"])
        self.assertEqual(result["records"][1:], records[1:])
        for before, after in zip(records, result["records"]):
            self.assertEqual({k: v for k, v in before.items() if k != "status"},
                             {k: v for k, v in after.items() if k != "status"})

    def test_malformed_record_fields_are_reported_atomically(self):
        for field, value in (("net", None), ("component", None), ("row", []), ("refdes", [])):
            records = [row(1)]
            records[0][field] = value
            self.assert_atomic_error(compute(records, []), records, "invalid_record")

    def test_duplicate_refdes_even_across_nets(self):
        records = [row(1), row(2, net="U2_TEST")]
        records[1]["refdes"] = "C1"
        self.assert_atomic_error(compute(records, [command()]), records, "duplicate_refdes")

    def test_duplicate_excel_rows(self):
        records = [row(1), row(2)]
        records[1]["row"] = records[0]["row"]
        self.assert_atomic_error(compute(records, [command()]), records, "duplicate_row")

    def test_unsupported_target_status_errors_but_other_rows_preserved(self):
        records = [row(1), row(2, component="RESISTOR", status="Enabled", net="")]
        records[1]["distance"] = None
        result = compute(records, [command()])
        self.assertTrue(result["ok"])
        self.assertEqual(result["records"][1], records[1])
        records[1].update(component=MID, net="U1_TEST")
        self.assert_atomic_error(compute(records, [command()]), records, "unsupported_status")

    def test_shortage_rolls_back_earlier_commands(self):
        records = [row(1), row(2, LARGE)]
        result = compute(records, [command(), command(LARGE, "D+2")])
        self.assert_atomic_error(result, records, "shortage")
        self.assertGreater(len(result["actions"]), 0)

    def test_negative_never_converts_or_counts_disabled(self):
        records = [row(1), row(2, LARGE, status="Automatic")]
        result = compute(records, [command(expression="D-1")], policies=[policy(MID, LARGE, donor_status="any")])
        self.assert_atomic_error(result, records, "shortage")

    def test_47uf_uses_farthest_disabled_1uf_after_existing_target(self):
        records = [row(1, XL), row(2, LARGE), row(3, LARGE), row(4, LARGE, status="Automatic")]
        result = compute(records, [command(XL, "A+2")], policies=[policy(XL, LARGE)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["kind"], a["refdes"]) for a in result["actions"]], [("command", "C1"), ("conversion", "C3")])
        self.assertEqual(active(result, XL), 2)
        self.assertEqual(active(result, LARGE), 1)

    def test_1uf_any_status_donor_and_farthest_compensation(self):
        records = [row(1, MID), row(2, MID), row(3, LARGE, status="Automatic"), row(4, MID, status="Automatic")]
        result = compute(records, [command(LARGE)], policies=[policy(LARGE, MID, donor_status="any", compensate=True)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["kind"], a["refdes"]) for a in result["actions"]], [("conversion", "C4"), ("compensation", "C2")])
        self.assertEqual(result["actions"][1]["direction"], "D")
        self.assertEqual(active(result, MID), 1)
        self.assertEqual(active(result, LARGE), 2)
        self.assertTrue(all(s["after_active"] == s["expected_active"] for s in result["summary"]))

    def test_multiple_donors_are_reserved_before_compensation(self):
        records = [row(1), row(2), row(3, status="Automatic"), row(4)]
        result = compute(records, [command(LARGE, "A+2")], policies=[policy(LARGE, MID, donor_status="any", compensate=True)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["kind"], a["refdes"]) for a in result["actions"]], [("conversion", "C4"), ("conversion", "C3"), ("compensation", "C2")])
        self.assertEqual(active(result, MID), 1)
        self.assertEqual(active(result, LARGE), 2)

    def test_compensation_shortage_is_atomic(self):
        records = [row(1, status="Automatic")]
        result = compute(records, [command(LARGE)], policies=[policy(LARGE, MID, donor_status="any", compensate=True)])
        self.assert_atomic_error(result, records, "compensation_shortage")

    def test_missing_donor_or_compensation_distance_is_atomic(self):
        records = [row(1), row(2, status="Automatic")]
        records[0]["distance"] = None
        result = compute(records, [command(LARGE)], policies=[policy(LARGE, MID, donor_status="any", compensate=True)])
        self.assert_atomic_error(result, records, "missing_distance")

    def test_10nf_converts_nearest_disabled_100nf(self):
        records = [row(1, status="Automatic"), row(2), row(3)]
        result = compute(records, [command(SMALL)], policies=[policy(SMALL, MID, direction="A")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["refdes"], "C2")
        self.assertEqual(active(result, MID), 1)

    def test_conversion_policy_is_never_implicit(self):
        records = [row(1, XL, status="Automatic"), row(2, LARGE)]
        self.assert_atomic_error(compute(records, [command(XL)]), records, "shortage")
        self.assertEqual(compute(records, [])["records"], records)

    def test_uncompensated_active_donor_is_rejected_atomically(self):
        records = [row(1, status="Automatic")]
        result = compute(records, [command(LARGE)], policies=[policy(LARGE, MID, donor_status="any")])
        self.assert_atomic_error(result, records, "active_compensation_required")
        donor_summary = next(s for s in result["summary"] if s["component"] == MID)
        self.assertEqual(donor_summary["expected_active"], 1)
        self.assertEqual(active(result, MID), 1)

    def test_compensation_preserves_previous_donor_command_target(self):
        records = [row(1), row(2), row(3), row(4, status="Automatic")]
        result = compute(records, [command(expression="A+1"), command(LARGE)],
                         policies=[policy(LARGE, MID, donor_status="any", compensate=True)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(active(result, MID), 2)

    def test_farther_automatic_1uf_is_taken_before_disabled_and_compensated_farthest(self):
        records = [row(1, LARGE), row(2, LARGE), row(3, LARGE, "Automatic")]
        result = compute(records, [command(XL)], policies=[policy(XL, LARGE, donor_status="any", compensate=True)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["kind"], a["refdes"]) for a in result["actions"]], [("conversion", "C3"), ("compensation", "C2")])
        self.assertEqual(active(result, XL), 1)
        self.assertEqual(active(result, LARGE), 1)
        self.assertEqual(result["records"][0], records[0])

    def test_missing_physical_donor_uses_disabled_intermediate_without_active_growth(self):
        records = [row(1), row(2)]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL)], policies=rules)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["refdes"], a["old_component"], a["new_component"], a["new_status"]) for a in result["actions"]],
                         [("C2", MID, LARGE, "Disabled"), ("C2", LARGE, XL, "Automatic")])
        self.assertIn("임시 Disabled", result["actions"][0]["reason"])
        self.assertEqual(active(result, XL), 1)
        self.assertEqual(active(result, LARGE), 0)
        self.assertEqual(active(result, MID), 0)
        self.assertEqual(len(result["changes"]), 1)

    def test_missing_compensation_1uf_is_created_from_100nf_preserving_both(self):
        records = [row(1), row(2, MID, "Automatic"), row(3, LARGE, "Automatic")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL)], policies=rules)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["kind"], a["refdes"]) for a in result["actions"]],
                         [("conversion", "C3"), ("conversion", "C2"), ("compensation", "C1")])
        self.assertIn("연쇄 활성 수량 보정", result["actions"][1]["reason"])
        self.assertEqual([active(result, comp) for comp in (MID, LARGE, XL)], [1, 1, 1])

    def test_multilevel_physical_and_active_shortages_preserve_all_counts(self):
        records = [row(1, SMALL), row(2, SMALL), row(3, SMALL), row(4, MID, "Automatic"), row(5, LARGE, "Automatic")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True),
                 policy(LARGE, MID, donor_status="any", compensate=True),
                 policy(MID, SMALL, donor_status="any", compensate=True)]
        result = compute(records, [command(XL, "D+2")], policies=rules)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([active(result, comp) for comp in (SMALL, MID, LARGE, XL)], [0, 1, 1, 2])
        self.assertEqual([r["component"] for r in result["records"]], [SMALL, MID, LARGE, XL, XL])
        self.assertTrue(all(s["expected_active"] == s["after_active"] for s in result["summary"]))
        self.assertEqual([(r["row"], r["refdes"], r["net"], r["distance"]) for r in result["records"]],
                         [(r["row"], r["refdes"], r["net"], r["distance"]) for r in records])
        self.assertTrue(any(a["new_status"] == "Disabled" and a["kind"] == "conversion" for a in result["actions"]))

    def test_chain_reserves_original_batch_before_restoring_active_donors(self):
        records = [row(1), row(2, LARGE), row(3, LARGE, "Automatic")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL, "D+2")], policies=rules)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([(a["refdes"], a["new_component"]) for a in result["actions"]], [("C3", XL), ("C2", XL), ("C1", LARGE)])
        self.assertEqual([active(result, comp) for comp in (MID, LARGE, XL)], [0, 1, 2])

    def test_lowest_compensation_shortage_rolls_back_every_intermediate_action(self):
        records = [row(1, MID, "Automatic")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL)], policies=rules)
        self.assert_atomic_error(result, records, "compensation_shortage")
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["new_status"], "Disabled")

    def test_lowest_physical_shortage_is_atomic_after_prior_command(self):
        records = [row(1, XL), row(2, MID)]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL, "A+3")], policies=rules)
        self.assert_atomic_error(result, records, "shortage")
        self.assertEqual(result["actions"][0]["kind"], "command")

    def test_unused_policy_cycle_rejected_before_any_changes(self):
        records = [row(1)]
        rules = [policy(XL, LARGE), policy(LARGE, SMALL), policy(SMALL, XL)]
        result = compute(records, [command()], policies=rules)
        self.assert_atomic_error(result, records, "policy_cycle")
        self.assertEqual(result["actions"], [])
        self.assertIn(XL, next(i["message"] for i in result["issues"] if i["code"] == "policy_cycle"))

    def test_chain_does_not_take_from_another_net_or_unmapped_type(self):
        records = [row(1, MID), row(2, LARGE, net="U2_TEST"), row(3, "UNMAPPED", distance=100), row(4, "RESISTOR", "Enabled", net="")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL)], policies=rules)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["records"][1:], records[1:])
        self.assertEqual(result["records"][0]["component"], XL)

    def test_chain_missing_distance_in_lower_donor_is_atomic(self):
        records = [row(1), row(2)]
        records[0]["distance"] = None
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=True)]
        result = compute(records, [command(XL)], policies=rules)
        self.assert_atomic_error(result, records, "missing_distance")

    def test_chain_compensation_tie_uses_stable_row_and_audits_direction(self):
        records = [row(1, LARGE, distance=10), row(2, LARGE, distance=10), row(3, LARGE, "Automatic", distance=20)]
        result = compute(records, [command(XL)], policies=[policy(XL, LARGE, donor_status="any", compensate=True)])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([a["refdes"] for a in result["actions"]], ["C3", "C1"])
        self.assertEqual(result["actions"][1]["direction"], "D")
        self.assertIn("distance_tie", {i["code"] for i in result["issues"]})

    def test_uncompensated_lower_policy_is_rejected_atomically(self):
        records = [row(1, MID, "Automatic")]
        rules = [policy(XL, LARGE, donor_status="any", compensate=True), policy(LARGE, MID, donor_status="any", compensate=False)]
        result = compute(records, [command(XL)], policies=rules)
        self.assert_atomic_error(result, records, "active_compensation_required")
        self.assertEqual([active(result, comp) for comp in (MID, LARGE, XL)], [1, 0, 0])
        donor_summary = next(s for s in result["summary"] if s["component"] == MID)
        self.assertEqual(donor_summary["expected_active"], 1)

    def test_chain_randomized_conservation_against_available_disabled_slots(self):
        rng = random.Random(947)
        components = [SMALL, MID, LARGE, XL]
        rules = [policy(target, donor, donor_status="any", compensate=True)
                 for donor, target in zip(components, components[1:])]
        for case in range(160):
            records = [row(i + 1, rng.choice(components), rng.choice(["Automatic", "Disabled"]),
                           distance=rng.randrange(10)) for i in range(rng.randrange(2, 20))]
            target_index, requested = rng.randrange(4), rng.randrange(1, 6)
            target = components[target_index]
            eligible_types = set(components[:target_index + 1])
            available = sum(r["status"] == "Disabled" and r["component"] in eligible_types for r in records)
            result = compute(records, [command(target, f"D+{requested}")], policies=rules)
            with self.subTest(case=case, target=target, requested=requested, available=available):
                self.assertEqual(result["ok"], available >= requested, result["issues"])
                if result["ok"]:
                    for component in components:
                        before = sum(r["component"] == component and r["status"] == "Automatic" for r in records)
                        self.assertEqual(active(result, component), before + (requested if component == target else 0))
                    for old, new in zip(records, result["records"]):
                        if old["component"] not in eligible_types:
                            self.assertEqual(old, new)
                        self.assertEqual((old["refdes"], old["net"], old["row"], old["distance"]),
                                         (new["refdes"], new["net"], new["row"], new["distance"]))
                else:
                    self.assertEqual(result["records"], records)
                    self.assertEqual(result["changes"], [])

    def test_rearrangement_includes_disabled_and_preserves_multiset(self):
        records = [row(1, LARGE), row(2, MID, status="Automatic"), row(3, SMALL)]
        result = compute(records, [], rearrange=True, priority=[SMALL, MID, LARGE], selected_nets=["U1_TEST"])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([r["component"] for r in result["records"]], [SMALL, MID, LARGE])
        self.assertEqual([r["status"] for r in result["records"]], ["Disabled", "Automatic", "Disabled"])
        reversed_result = compute(records, [], rearrange=True, priority=[LARGE, MID, SMALL], selected_nets=["U1_TEST"])
        self.assertTrue(reversed_result["ok"])
        self.assertEqual(reversed_result["records"], records)

    def test_status_travels_with_component_rearrangement(self):
        records = [row(1, LARGE, "Automatic"), row(2, MID), row(3, LARGE), row(4, MID, "Automatic")]
        result = compute(records, [], rearrange=True, priority=[MID, LARGE], selected_nets=["U1_TEST"])
        self.assertTrue(result["ok"])
        self.assertEqual([(r["component"], r["status"]) for r in result["records"]],
                         [(MID, "Disabled"), (MID, "Automatic"), (LARGE, "Automatic"), (LARGE, "Disabled")])

    def test_rearrangement_scope_is_explicit_and_unselected_untouched(self):
        records = [row(1, LARGE), row(2, MID), row(3, SMALL), row(4, LARGE, net="U2_TEST"), row(5, MID, net="U2_TEST")]
        self.assert_atomic_error(compute(records, [], rearrange=True, priority=[MID, LARGE]), records, "invalid_scope")
        result = compute(records, [], rearrange=True, priority=[MID, LARGE], selected_nets=["U1_TEST"])
        self.assertTrue(result["ok"])
        for index in (2, 3, 4):
            self.assertEqual(result["records"][index], records[index])
        self.assertEqual([(r["refdes"], r["net"], r["row"], r["distance"]) for r in result["records"]],
                         [(r["refdes"], r["net"], r["row"], r["distance"]) for r in records])

    def test_commands_then_rearrangement_counts_remain_independent(self):
        records = [row(1, LARGE), row(2, MID), row(3, MID)]
        result = compute(records, [command()], rearrange=True, priority=[MID, LARGE], selected_nets=["U1_TEST"])
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(active(result, MID), 1)
        self.assertEqual(result["records"][0]["status"], "Automatic")

    def test_ties_stable_and_warning_audited(self):
        records = [row(1, distance=10), row(2, distance=10)]
        result = compute(records, [command(expression="D+1")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["refdes"], "C1")
        self.assertIn("distance_tie", {i["code"] for i in result["issues"]})

    def test_unknown_net_and_invalid_rules_rejected(self):
        records = [row(1)]
        self.assert_atomic_error(compute(records, [command(net="MISSING")]), records, "unknown_net")
        self.assert_atomic_error(compute(records, [], policies=[policy(MID, MID)]), records, "conflicting_policy")
        self.assert_atomic_error(compute(records, [], policies=[policy(LARGE, MID), policy(LARGE, SMALL)]), records, "conflicting_policy")
        self.assert_atomic_error(compute(records, [], policies=[{}]), records, "invalid_policy")
        self.assert_atomic_error(compute(records, [], rearrange=True, priority=[MID, MID], selected_nets=["U1_TEST"]), records, "invalid_priority")


if __name__ == "__main__":
    unittest.main()
