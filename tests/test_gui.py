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
