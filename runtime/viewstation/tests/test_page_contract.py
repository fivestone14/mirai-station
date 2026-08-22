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
    assert "visibilitychange" in PAGE           # and re-checks the moment the tab comes back


def test_page_has_the_payload_tab_wired():
    assert 'data-tab="payload"' in PAGE and 'id="p-payload"' in PAGE
    assert "const PAYLOAD = {" in PAGE
    assert "/api/sndk/payload?user=" in PAGE
    assert "PAYLOAD.show()" in PAGE             # switchTab mounts it


def test_step_seven_explains_the_wake_gate():
    """The pipeline's STEP 7 is the only step most scans never reach, so it
    carries the gate in plain words — built from the server's numbers, and
    advertised, because a card that hides something has to look like it does."""
    assert "PAYLOAD.gateTip()" in PAGE          # the tooltip is rendered into the card
    assert "PAYLOAD.data.gates" in PAGE         # ...from the payload's own gate block, not retyped
    assert 'class="cue"' in PAGE                # the hover/tap cue
    assert "PAYLOAD.tipOpen" in PAGE            # tap works where there is no hover (tablet)


def test_memory_view_is_two_columns_with_a_spine():
    """The Memory redesign (08-22) has three pieces a later edit must not quietly drop:
    the left rail's tier switch (the <select> it replaced is gone), the spine that draws
    the session down the gutter, and the wake badge that says in one sentence why the
    model looked. A day-scoped ask must also keep carrying its date — the history CLI
    reads one file per session, so an ask with no date silently searches today."""
    assert 'id="mem-tiers"' in PAGE and 'class="mem-side"' in PAGE
    assert 'id="mem-tier"' not in PAGE           # the old <select> is retired
    assert "MEM.setTier(" in PAGE and "MEM.showDay(" in PAGE
    assert "drawSpine()" in PAGE and 'id="mem-spine"' in PAGE
    assert "MEM.WAKE" in PAGE and "'gate event'" in PAGE
    assert "'Short term'" in PAGE and "'Medium term'" in PAGE and "'Long term'" in PAGE
    assert "if(tier==='slices'){ const d=MEM.sel||MEM.newestDay(); if(d){ MEM.sel=d; p.date=d; } }" in PAGE

def test_the_memory_view_never_asks_for_a_page_the_route_will_refuse():
    """server.py validates every flag before the CLI sees it, so a limit over
    _MEMORY_MAX_LIMIT is not a bigger page — it is {"error": "bad query"} and an empty
    view. The page's own cap must track the server's."""
    import re
    import server
    caps = {int(n) for n in re.findall(r"limit:\s*'(\d+)'", PAGE)}
    caps |= {int(n) for n in re.findall(r"MAX:(\d+)", PAGE)}
    assert caps, "the page asks for no page size at all"
    assert max(caps) <= server._MEMORY_MAX_LIMIT
