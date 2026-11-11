import os
import sys

import pytest

from decap_manager.help_viewer import DEFAULT_HELP, parse_help_html


def test_bundled_help_has_current_sections_and_internal_links():
    title, blocks = parse_help_html(DEFAULT_HELP)
    assert title == "Decap Adjustment 도움말"
    headings = ["".join(run["text"] for run in block["runs"])
                for block in blocks if block["type"] == "heading"]
    assert "1 / 거리 기준 · 수량 조절" in headings
    assert "2 / 도면 · 직접 선택" in headings
    assert "부품 크기·용량" in headings
    links = [run["href"] for block in blocks if block["type"] != "table" for run in block["runs"]]
    assert "#commands" in links and "#board" in links
    assert all(href is None or href.startswith("#") for href in links)


def test_parser_keeps_external_links_inert_and_ignores_scripts(tmp_path):
    page = tmp_path / "fixture.html"
    page.write_text("""<html><head><title>Fixture</title></head><body>
      <h2 id="inside">Heading</h2><p><a href="#inside">Internal</a>
      <a href="https://example.invalid">External label</a></p>
      <script>alert('must not render')</script></body></html>""", encoding="utf-8")
    title, blocks = parse_help_html(page)
    runs = [run for block in blocks for run in block.get("runs", [])]
    assert title == "Fixture"
    assert any(run["text"] == "Internal" and run["href"] == "#inside" for run in runs)
    assert any(run["text"] == "External label" and run["href"] is None for run in runs)
    assert "".join(run["text"] for run in runs).find("must not render") == -1


@pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="Tk requires a display")
def test_viewer_styles_whole_heading_and_searches(tmp_path):
    import tkinter as tk
    import tkinter.font as tkfont

    from decap_manager.help_viewer import HtmlHelpViewer

    page = tmp_path / "fixture.html"
    page.write_text("""<html><head><title>Fixture</title></head><body>
      <h2 id="inside">Large <strong>bold</strong> heading</h2>
      <p><a href="#inside">Internal</a><a href="https://example.invalid">External</a></p>
      <script>alert('must not render')</script></body></html>""", encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    viewer = HtmlHelpViewer(root, page)
    try:
        root.update_idletasks()
        heading_range = viewer.text.tag_prevrange("heading2", "end")
        heading = viewer.text.get(*heading_range)
        assert "Large bold heading" in heading
        assert tkfont.Font(root=root, font=viewer.text.tag_cget("heading2", "font")).actual("size") > 10
        assert "must not render" not in viewer.text.get("1.0", "end")
        assert all(not tag.startswith("link_http") for tag in viewer.text.tag_names())
        viewer.query.set("bold")
        viewer.search()
        assert viewer.text.tag_ranges("search")
        viewer.goto_anchor("inside")
    finally:
        viewer.destroy()
        root.destroy()
