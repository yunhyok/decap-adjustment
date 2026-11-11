"""Drive the board's canvas and tree selection through Tk events."""

import os
import sys
import tkinter as tk
from tkinter import ttk

import pytest

from decap_manager.board_view import BoardView


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="Tk requires a display",
)


@pytest.fixture
def board():
    root = tk.Tk()
    root.geometry("980x600")
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    calls = []
    view = BoardView(root, lambda refs, status: calls.append((refs, status)))
    view.pack(fill="both", expand=True)
    rows = [
        dict(refdes="C1", net="U0_VDD", component="CAP_0603M_100NF", status="Enabled"),
        dict(refdes="C2", net="U0_AUX", component="CAP_1004_1UF___10", status="Disabled"),
        dict(refdes="C3", net="U1_VDD", component="CAP_0603M_4U7F", status="unknown"),
        dict(refdes="C4", net="U0_VDD", component="CAP_0603M_100NF", status="Disabled"),
        dict(refdes="C5", net="U0_AUX", component="CAP_1004_1UF___10", status="Automatic"),
        dict(refdes="C6", net="", component="CAP_0603M_10NF", status="Unknown"),
    ]
    geometry = {}
    for ref, x, side, site, channel in (
        ("C1", 100, "Top", "U0", "VDD"),
        ("C2", 300, "Top", "U0", "AUX"),
        ("C3", 500, "Top", "U1", "VDD"),
        ("C4", 100, "Bottom", "U0", "VDD"),
        ("C5", 400, "Bottom", "U0", "AUX"),
    ):
        geometry[ref] = dict(x=x, y=100, side=side, pins=[(x - 10, 100), (x + 10, 100)],
                             channel=channel, site=site)
    geometry["U0"] = dict(x=0, y=20, side="Top", pins=[(0, 20), (10, 20)])
    geometry["C2"].update(size=(30, 12), angle=0)
    view.set_records(rows, geometry)
    root.update()
    yield root, view, rows, geometry, calls
    root.destroy()


def _gesture(root, view, start, end=None, extend=False):
    start_x, start_y = map(round, start)
    end_x, end_y = map(round, end or start)
    state = 4 if extend else 0
    view.canvas.event_generate("<ButtonPress-1>", x=start_x, y=start_y, state=state)
    if end is not None:
        view.canvas.event_generate("<B1-Motion>", x=end_x, y=end_y, state=state | 256)
    view.canvas.event_generate("<ButtonRelease-1>", x=end_x, y=end_y, state=state | 256)
    root.update()


def test_canvas_ctrl_and_rectangle_update_tree_selection(board):
    root, view, _, _, _ = board
    body = next(item for item, ref in view._items.items() if ref == "C2" and view.canvas.type(item) == "polygon")
    coords = view.canvas.coords(body)
    assert max(coords[::2]) - min(coords[::2]) == pytest.approx(30 * view._scale)
    assert max(coords[1::2]) - min(coords[1::2]) == pytest.approx(12 * view._scale)
    c1, c2, c3 = (view._centers[ref] for ref in ("C1", "C2", "C3"))
    _gesture(root, view, c1)
    assert view.tree.selection() == ("C1",)
    _gesture(root, view, c2, extend=True)
    assert set(view.tree.selection()) == {"C1", "C2"}
    _gesture(root, view, c2, extend=True)
    assert view.tree.selection() == ("C1",)

    rectangle = ((c2[0] - 15, c2[1] - 15), (c3[0] + 15, c3[1] + 15))
    _gesture(root, view, *rectangle, extend=True)
    assert set(view.tree.selection()) == {"C1", "C2", "C3"}
    _gesture(root, view, *rectangle)
    assert set(view.tree.selection()) == {"C2", "C3"}
    assert view.counts.get().endswith("선택 2")

    reference_x, reference_y = view._offset
    _gesture(root, view, (reference_x, reference_y - 20 * view._scale))
    assert view.tree.selection() == ()


def test_tree_and_filtered_selection_apply_only_visible_eligible_changes(board):
    root, view, rows, geometry, calls = board
    view.tree.selection_set("C1", "C2", "C3")
    root.update()
    assert view._selected == {"C1", "C2", "C3"}
    assert view.disable_button["state"] == "normal"
    assert view.activate_button["state"] == "normal"
    view.activate_button.invoke()
    assert calls == [({"C2"}, "Automatic")]
    assert rows[1]["status"] == "Disabled"

    view.query.set("Vdd u1 0603m 4700 nF")
    root.update()
    assert view.tree.get_children() == view.tree.selection() == ("C3",)
    assert set(view._centers) == {"C3"}
    assert view.activate_button["state"] == "disabled"
    view.disable_button.invoke()
    assert calls[-1] == ({"C3"}, "Disabled")

    rows[2]["status"] = "Disabled"
    view.set_records(rows, geometry)
    root.update()
    assert view.tree.selection() == ("C3",)
    assert view.tree.set("C3", "status") == "Disabled"
    assert "활성 0" in view.counts.get() and "비활성 1" in view.counts.get()
    assert view.disable_button["state"] == "disabled"
    assert view.activate_button["state"] == "normal"
    view.query.set("")
    root.update()
    assert view.tree.selection() == ("C3",)


def test_side_filter_mirror_zoom_and_unknown_rows(board):
    root, view, _, _, calls = board
    view.tree.selection_set("C1")
    root.update()
    view.side_buttons["Bottom"].invoke()
    root.update()
    assert view.tree.get_children() == ("C4", "C5")
    assert view.tree.selection() == ()
    assert set(view._centers) == {"C4", "C5"}
    assert view._centers["C4"][0] < view._centers["C5"][0]

    view.mirror_button.invoke()
    root.update()
    assert view._centers["C4"][0] > view._centers["C5"][0]
    x, y = map(round, view._centers["C4"])
    old_scale = view._scale
    view.canvas.event_generate("<MouseWheel>", x=x, y=y, delta=120)
    root.update()
    assert view._scale > old_scale
    assert view._centers["C4"] == pytest.approx((x, y), abs=1)
    center = view._centers["C4"]
    view.canvas.event_generate("<ButtonPress-3>", x=100, y=100)
    view.canvas.event_generate("<B3-Motion>", x=130, y=120, state=1024)
    view.canvas.event_generate("<ButtonRelease-3>", x=130, y=120)
    root.update()
    assert view._centers["C4"] == pytest.approx((center[0] + 30, center[1] + 20))

    view.side_buttons["Unknown"].invoke()
    root.update()
    assert view.tree.get_children() == ("C6",)
    assert not view._centers
    assert "좌표 없음 1" in view.counts.get()
    view.tree.selection_set("C6")
    root.update()
    view.activate_button.invoke()
    view.disable_button.invoke()
    assert not calls
    view.side_buttons["Top"].invoke()
    root.update()
    assert view.tree.selection() == ()


def test_authoritative_component_metadata_controls_columns_and_search(board):
    root, view, rows, geometry, _ = board
    rows[0].update(size="1005", capacitance_pf=10_000, metadata_source="SPICE")
    rows[2].update(size="", capacitance_pf=None, metadata_source="미확인")
    geometry["C3"]["size"] = "0603M"
    view.set_records(rows, geometry)
    root.update()
    assert view.tree.set("C1", "size") == "1005"
    assert view.tree.set("C1", "capacity") == "10 nF"
    assert view.tree.set("C1", "source") == "SPICE"
    assert view.tree.set("C3", "size") == view.tree.set("C3", "capacity") == "—"
    view.query.set("0.01 µF 1005")
    assert view.tree.get_children() == ("C1",)
    view.query.set("100 nF")
    assert view.tree.get_children() == ()
    view.query.set("4u7f")
    assert view.tree.get_children() == ()
