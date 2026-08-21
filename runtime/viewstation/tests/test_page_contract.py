"""The page's self-refresh contract, pinned as strings so a later edit that slices
the script between two markers (it happened on 08-21: the hanging-indent rewrite
dropped the version poller) fails the suite instead of silently shipping a page
that can never show the refresh pill."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()


def test_page_has_the_refresh_pill_and_its_poller():
    assert 'id="reload-bar"' in PAGE            # the pill
    assert 'id="ver-stamp"' in PAGE             # the visible "page HH:MM · checked HH:MM" stamp
    assert "fetch('/api/version'" in PAGE       # the poller asks the server
    assert "let seen=null" in PAGE              # ...and compares against the version it loaded with
    assert "setInterval(tick, 10000)" in PAGE   # every 10 s
    assert "visibilitychange" in PAGE           # and re-checks when the tab comes back


def test_page_has_the_payload_tab_wired():
    assert 'data-tab="payload"' in PAGE and 'id="p-payload"' in PAGE
    assert "const PAYLOAD = {" in PAGE
    assert "/api/sndk/payload?user=" in PAGE
    assert "PAYLOAD.show()" in PAGE             # switchTab mounts it
