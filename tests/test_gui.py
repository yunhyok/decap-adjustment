import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="Tk requires a display")


@pytest.fixture
def app():
    from decap_manager.gui import App
    instance = App()
    instance.withdraw()
    yield instance
    instance.destroy()


def test_bundled_integration():
    from decap_manager.self_test import self_test
    report = self_test()
    assert report["ok"]
    assert report["rows"] == 33
    assert report["changed_refdes"] == 6


def test_manual_priority_changes_placement_and_invalidates_preview(app):
    app.load_examples()
    app.mode.set("direct")
    app.rearrange.set(True)
    app.net_list.selection_set(0)
    app.preview()
    assert app.result["ok"]
    first = app.priority_table.get_children()[0]
    app.priority_table.selection_set(first)
    app.move_priority(1)
    assert app.result is None
    assert "disabled" in app.save_button.state()
    app.preview()
    assert app.result["ok"]
    assert app.result["changes"]
    selected_net = app.net_list.get(0)
    assert all(c["net"] == selected_net for c in app.result["changes"])


def test_capacity_edit_sorts_included_components_only(app, monkeypatch):
    app.load_examples()
    first, second, *_ = app.priority_table.get_children()
    excluded = app.priority_table.item(second, "values")[0]
    app.priority_table.selection_set(second)
    app.exclude_priority()
    app.priority_table.selection_set(first)
    monkeypatch.setattr("decap_manager.gui.simpledialog.askfloat", lambda *a, **k: 100000000)
    app.edit_capacity()
    values = [app.priority_table.item(i, "values")[0] for i in app.priority_table.get_children()]
    assert excluded not in values
    assert values[-1] == "CAP_0603M_10NF"


def test_changed_source_cannot_export(app, monkeypatch, tmp_path):
    app.load_examples()
    app.preview()
    assert app.result["ok"]
    errors = []
    monkeypatch.setattr("decap_manager.gui.filedialog.askdirectory", lambda **k: str(tmp_path))
    monkeypatch.setattr("decap_manager.gui.fingerprint", lambda path: "changed")
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda title, message, **k: errors.append(message))
    app.save()
    assert app.result is None
    assert errors and "변경" in errors[0]
    assert not list(tmp_path.iterdir())


def test_policy_only_priority_is_removed_when_disabled_or_deleted(app):
    app.load_examples()
    app.mode.set("direct")
    app.rearrange.set(True)
    app.net_list.selection_set(0)
    target = "CAP_0603M_22UF"
    app.policies = [dict(target=target, donor="CAP_0603M_1UF", direction="D", donor_status="Disabled", compensate=False)]
    app.use_policies.set(True)
    app.render_policies()
    assert target in app.capacities
    app.preview()
    assert app.result["ok"]
    app.use_policies.set(False)
    app.render_policies()
    assert target not in app.capacities
    app.preview()
    assert app.result["ok"]
    app.use_policies.set(True)
    app.render_policies()
    app.policy_table.selection_set(app.policy_table.get_children()[0])
    app.remove_policy()
    assert target not in app.capacities
    app.preview()
    assert app.result["ok"]


def _shortage_setup(app):
    from copy import deepcopy
    from decap_manager.engine import parse_capacitance

    app.load_examples()
    components = ["CAP_10NF", "CAP_100NF", "CAP_1UF", "CAP_1UF", "CAP_4U7F", "CAP_1UF"]
    app.records = [dict(row=index + 2, refdes=f"C{index + 1}", net="OTHER" if index == 5 else "DEMO",
                        component=component, status="Automatic" if index == 4 else "Disabled",
                        distance=distance)
                   for index, (component, distance) in enumerate(zip(components, [10, 20, 30, 90, 100, 200]))]
    app.capacities = {component: parse_capacitance(component) for component in components}
    app.reset_priority()
    app.manual = [dict(net="DEMO", component="CAP_4U7F", expression="D+1", source="Synthetic direct command")]
    app.mode.set("direct")
    return deepcopy(app.records)


def _capture_recovery_prompt(monkeypatch, answer, before_answer=None):
    prompts = []
    def ask(title, message, **kwargs):
        prompts.append((title, message, kwargs))
        if before_answer:
            before_answer()
        return answer
    monkeypatch.setattr("decap_manager.gui.messagebox.askyesno", ask)
    monkeypatch.setattr("decap_manager.gui.messagebox.showerror", lambda *args, **kwargs: None)
    monkeypatch.setattr("decap_manager.gui.messagebox.showinfo", lambda *args, **kwargs: None)
    return prompts


@pytest.mark.parametrize("use_alias", [False, True])
def test_shortage_recovery_requires_approval_and_adds_only_used_rule(app, monkeypatch, use_alias):
    original = _shortage_setup(app)
    if use_alias:
        app.aliases = {"EXPLICIT_ALIAS": "CAP_4U7F"}
        app.manual[0]["component"] = "EXPLICIT_ALIAS"

    def before_approval():
        assert app.records == original
        assert app.policies == []
        assert not app.use_policies.get()
        assert "disabled" in app.save_button.state()

    prompts = _capture_recovery_prompt(monkeypatch, True, before_approval)
    app.preview()
    assert len(prompts) == 1
    _, proposal, options = prompts[0]
    assert str(options.get("default", "")).lower() == "no"
    assert all(text in proposal for text in ("CAP_1UF", "CAP_4U7F", "Disabled", "1개"))
    assert "먼" in proposal or "D" in proposal
    assert "보정" in proposal
    assert "CAP_10NF" not in proposal and "CAP_100NF" not in proposal
    assert app.policies == [dict(target="CAP_4U7F", donor="CAP_1UF", direction="D", donor_status="any", compensate=True)]
    assert app.use_policies.get()
    assert app.result["ok"]
    assert [(change["refdes"], change["new_component"], change["new_status"]) for change in app.result["changes"]] == [("C4", "CAP_4U7F", "Automatic")]
    assert app.records == original
    assert app.result["records"][-1] == original[-1]
    assert "disabled" not in app.save_button.state()


def test_declined_shortage_recovery_keeps_atomic_failure(app, monkeypatch):
    original = _shortage_setup(app)
    prompts = _capture_recovery_prompt(monkeypatch, False)
    app.preview()
    assert len(prompts) == 1
    assert not app.result["ok"]
    assert app.result["records"] == original == app.records
    assert app.result["changes"] == []
    assert app.policies == [] and not app.use_policies.get()
    assert "shortage" in {issue["code"] for issue in app.result["issues"]}
    assert "disabled" in app.save_button.state()


@pytest.mark.parametrize("reason", ["ambiguous", "all_smaller_positions_active"])
def test_unusable_recovery_does_not_offer_success(app, monkeypatch, reason):
    from copy import deepcopy
    _shortage_setup(app)
    if reason == "ambiguous":
        app.records[2]["component"] = "CAP_OTHER_1UF"
        app.capacities["CAP_OTHER_1UF"] = 1_000_000
    else:
        for record in app.records:
            if record["net"] == "DEMO":
                record["status"] = "Automatic"
    original = deepcopy(app.records)
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert not prompts
    assert not app.result["ok"]
    assert app.result["records"] == original == app.records
    assert not app.result["changes"] and not app.policies
    assert "disabled" in app.save_button.state()
    guidance = app.details.get("1.0", "end")
    assert "규칙" in guidance or "배치" in guidance


def test_recovery_preserves_and_runs_existing_active_custom_policy(app, monkeypatch):
    original = _shortage_setup(app)
    custom = dict(target="CAP_10NF", donor="CAP_100NF", direction="D", donor_status="any", compensate=False)
    app.policies = [custom.copy()]
    app.use_policies.set(True)
    app.manual.insert(0, dict(net="DEMO", component="CAP_10NF", expression="A+2", source="Synthetic custom rule"))
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert len(prompts) == 1
    assert app.result["ok"]
    assert app.policies[0] == custom and len(app.policies) == 2
    assert app.policies[1]["target"] == "CAP_4U7F"
    assert "CAP_10NF" not in prompts[0][1] and "CAP_100NF" not in prompts[0][1]
    assert {change["refdes"] for change in app.result["changes"]} == {"C1", "C2", "C4"}
    assert app.records == original
    assert all(row["after_active"] == row["expected_active"] for row in app.result["summary"])
    assert "disabled" not in app.save_button.state()


def test_disabled_custom_policy_is_never_automatically_enabled(app, monkeypatch):
    original = _shortage_setup(app)
    custom = dict(target="CAP_10NF", donor="CAP_100NF", direction="D", donor_status="any", compensate=False)
    app.policies = [custom.copy()]
    app.use_policies.set(False)
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert not prompts
    assert not app.result["ok"]
    assert app.policies == [custom] and not app.use_policies.get()
    assert app.result["records"] == original == app.records
    assert "disabled" in app.save_button.state()
    assert "규칙" in app.details.get("1.0", "end")


def test_recovery_never_replaces_existing_target_policy(app, monkeypatch):
    original = _shortage_setup(app)
    custom = dict(target="CAP_4U7F", donor="CAP_100NF", direction="A", donor_status="Disabled", compensate=False)
    app.policies = [custom.copy()]
    app.use_policies.set(True)
    app.manual[0]["expression"] = "D+3"
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert not prompts
    assert not app.result["ok"]
    assert app.policies == [custom] and app.use_policies.get()
    assert app.result["records"] == original == app.records
    assert not app.result["changes"]
    assert "disabled" in app.save_button.state()


@pytest.mark.parametrize("can_compensate", [True, False])
def test_one_uf_recovery_checks_active_donor_compensation_before_offer(app, monkeypatch, can_compensate):
    from copy import deepcopy
    _shortage_setup(app)
    app.manual[0]["component"] = "CAP_1UF"
    for record in app.records:
        if record["component"] == "CAP_1UF" and record["net"] == "DEMO":
            record["status"] = "Automatic"
    app.records.append(dict(row=8, refdes="C7", net="DEMO", component="CAP_100NF", status="Automatic", distance=120))
    if not can_compensate:
        app.records[1]["status"] = "Automatic"
        app.records[0]["status"] = "Automatic"
    original = deepcopy(app.records)
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert app.records == original
    if not can_compensate:
        assert not prompts and not app.result["ok"]
        assert app.result["records"] == original and not app.result["changes"]
        assert not app.policies and "disabled" in app.save_button.state()
        return
    assert len(prompts) == 1 and "보정" in prompts[0][1]
    assert app.result["ok"] and app.policies[0]["compensate"]
    assert [(action["kind"], action["refdes"]) for action in app.result["actions"]] == [
        ("conversion", "C7"), ("compensation", "C2")]
    donor_count = lambda rows: sum(row["net"] == "DEMO" and row["component"] == "CAP_100NF" and row["status"] == "Automatic" for row in rows)
    assert donor_count(app.result["records"]) == donor_count(original) == 1
    assert "disabled" not in app.save_button.state()


def test_recovery_omits_commanded_rule_when_existing_positions_are_enough(app, monkeypatch):
    original = _shortage_setup(app)
    app.manual.insert(0, dict(net="DEMO", component="CAP_10NF", expression="A+1", source="Synthetic sufficient positions"))
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert app.result["ok"] and len(prompts) == 1
    assert [policy["target"] for policy in app.policies] == ["CAP_4U7F"]
    assert "CAP_10NF" not in prompts[0][1] and "CAP_100NF" not in prompts[0][1]
    assert {change["refdes"] for change in app.result["changes"]} == {"C1", "C4"}
    assert app.records == original


def _move_intermediates_to_other_net(app, target="CAP_4U7F"):
    from copy import deepcopy
    _shortage_setup(app)
    for record in app.records:
        if record["component"] == "CAP_1UF" or target == "CAP_22UF" and record["component"] == "CAP_4U7F":
            record["net"] = "OTHER"
    if target == "CAP_22UF":
        app.records.append(dict(row=8, refdes="C7", net="DEMO", component=target, status="Automatic", distance=150))
        app.capacities[target] = 22_000_000
        app.reset_priority()
    app.manual[0]["component"] = target
    return deepcopy(app.records)


@pytest.mark.parametrize("target, used_targets", [
    ("CAP_4U7F", {"CAP_1UF", "CAP_4U7F"}),
    ("CAP_22UF", {"CAP_1UF", "CAP_4U7F", "CAP_22UF"}),
])
def test_cascade_proposes_transitive_rules_and_recomputes_identically(app, monkeypatch, target, used_targets):
    from copy import deepcopy
    original = _move_intermediates_to_other_net(app, target)
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert len(prompts) == 1 and app.result["ok"]
    assert app.records == original
    assert {policy["target"] for policy in app.policies} == used_targets
    assert all(policy["donor_status"] == "any" and policy["compensate"] for policy in app.policies)
    assert [(change["refdes"], change["new_component"], change["new_status"]) for change in app.result["changes"]] == [("C2", target, "Automatic")]
    intermediate = [action for action in app.result["actions"] if action["kind"] == "conversion" and action["new_component"] != target]
    assert {action["new_component"] for action in intermediate} == used_targets - {target}
    assert all(action["new_status"] == "Disabled" for action in intermediate)
    proposal = prompts[0][1]
    assert "CAP_100NF" in proposal and all(component in proposal for component in used_targets)
    for component in used_targets:
        line = next(line for line in proposal.splitlines() if f"→ {component}:" in line)
        expected_counts = "Automatic 1개 / Disabled 0개" if component == target else "Automatic 0개 / Disabled 1개"
        assert expected_counts in line
    assert "CAP_10NF" not in proposal
    verified_trial = deepcopy(app.result)
    app.preview()
    assert len(prompts) == 1
    assert app.result["ok"]
    for key in ("records", "changes", "summary", "actions"):
        assert app.result[key] == verified_trial[key]
    assert app.records == original


def test_declining_cascade_keeps_all_intermediate_rules_unapplied(app, monkeypatch):
    original = _move_intermediates_to_other_net(app)
    prompts = _capture_recovery_prompt(monkeypatch, False)
    app.preview()
    assert len(prompts) == 1 and "CAP_100NF → CAP_1UF" in prompts[0][1]
    assert not app.result["ok"] and not app.result["changes"]
    assert app.result["records"] == original == app.records
    assert not app.policies and not app.use_policies.get()
    assert "disabled" in app.save_button.state()


def test_farthest_automatic_donor_precedes_nearer_disabled_and_compensates_far(app, monkeypatch):
    from copy import deepcopy
    _shortage_setup(app)
    app.records[3]["status"] = "Automatic"
    app.records.append(dict(row=8, refdes="C7", net="DEMO", component="CAP_1UF", status="Disabled", distance=70))
    original = deepcopy(app.records)
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert len(prompts) == 1 and app.result["ok"]
    assert [(action["kind"], action["refdes"], action["direction"]) for action in app.result["actions"]] == [
        ("conversion", "C4", "D"), ("compensation", "C7", "D")]
    assert app.result["records"][2] == original[2]
    assert sum(record["net"] == "DEMO" and record["component"] == "CAP_1UF" and record["status"] == "Automatic"
               for record in app.result["records"]) == 1
    assert app.records == original
    assert len(app.policies) == 1 and app.policies[0]["target"] == "CAP_4U7F"


def test_recommended_rules_are_adjacent_and_do_not_skip_ambiguous_capacity(app, monkeypatch):
    _move_intermediates_to_other_net(app, "CAP_22UF")
    app.capacities["UNRECOGNIZED_PART"] = None
    policies = app.recommended_policies()
    assert {(policy["target"], policy["donor"]) for policy in policies} == {
        ("CAP_22UF", "CAP_4U7F"), ("CAP_4U7F", "CAP_1UF"),
        ("CAP_1UF", "CAP_100NF"), ("CAP_100NF", "CAP_10NF")}
    assert all(policy["direction"] == "D" and policy["donor_status"] == "any" and policy["compensate"] for policy in policies)
    app.capacities["CAP_ALTERNATE_1UF"] = 1_000_000
    app.records.append(dict(row=9, refdes="C8", net="OTHER", component="CAP_ALTERNATE_1UF", status="Disabled", distance=300))
    policies = app.recommended_policies()
    assert {(policy["target"], policy["donor"]) for policy in policies} == {
        ("CAP_22UF", "CAP_4U7F"), ("CAP_100NF", "CAP_10NF")}
    prompts = _capture_recovery_prompt(monkeypatch, True)
    app.preview()
    assert not prompts and not app.result["ok"]
    assert not app.policies and not app.result["changes"]
    assert "disabled" in app.save_button.state()
