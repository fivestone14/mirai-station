"""/api/version — the page's build id is index.html's mtime: it changes when the
file is touched and never raises when the file is missing."""
import os
import time

import server


def test_version_is_the_page_mtime_and_changes_on_touch(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<title>a</title>")
    monkeypatch.setattr(server, "STATIC", tmp_path)
    v1 = server._page_version()
    assert v1 == str(page.stat().st_mtime_ns) and v1 != "0"
    os.utime(page, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))   # a later write
    v2 = server._page_version()
    assert v2 != v1


def test_version_survives_a_missing_page(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATIC", tmp_path)
    assert server._page_version() == "0"
