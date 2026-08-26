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

def test_a_chart_callout_can_never_reach_the_right_hand_readouts():
    """08-22, seen on the SNDK chart: the in-plot callout "Abs Gamma 3 \u00b7 1,590" printed hard
    against "Tipping point" in the value gutter \u2014 two unrelated facts fused into one run of
    text. Trusting the plot edge (lineEnd-4) was the mistake: the tick lane and the value
    column both start within a few pixels of it. Three things keep it from coming back and
    all three are pinned here, because a search rule is easy to reintroduce without them."""
    assert "gx1=lineEnd-px(12)" in PAGE                       # a real margin, not the bare edge
    assert "mark(gx1, gy0, gx0+nc*CW, gy1)" in PAGE           # ...and the strip past it is no-go
    assert "put.x=Math.max(gx0, Math.min(put.x, gx1-tw))" in PAGE   # ...and every branch is clamped

def test_the_manifest_link_carries_the_login():
    """08-23: the public front door asked for the password TWICE. One wall, two requests \u2014 the
    page carried the Basic-auth header, the manifest did not, because a manifest fetch is
    specified to omit credentials unless the link says otherwise, so Caddy 401'd it and the
    browser prompted again. Pinned: dropping the attribute brings the double prompt back."""
    assert 'rel="manifest"' in PAGE
    assert 'href="/manifest.webmanifest" crossorigin="use-credentials"' in PAGE


def test_mirai_caption_says_when_and_how_long_ago():
    """08-26: the thought-line caption carries the clock the read was issued at and how long
    ago, measured from reading_ts against the wall clock (the row's own reading_age_min froze
    between scans and all evening after the close). Four pieces a later edit must not drop:
    the stamp tspans, the in-place ticker on the spot poll, the hover card's live token fill,
    and the pure helper both of them read from."""
    assert 'class="snk-ago"' in PAGE and 'class="snk-when"' in PAGE   # the caption's second voice
    assert "data-since=" in PAGE                                       # the stamp the ticker/tip read
    assert "SNDK.tickAges();" in PAGE and "tickAges(){" in PAGE        # re-ticked on the spot poll
    assert "snkMinsSince(since, now)" in PAGE                          # age is measured, not read
    assert "{{ago}}¦m:{{pct}}¦f:{{left}}" in PAGE                      # the card's live tokens...
    assert "snkLapseFill(snkMinsSince(sinceEl.dataset.since))" in PAGE  # ...filled at hover time
    assert "function snkLapseFill(" in PAGE and "SNK_HORIZON_MIN=30" in PAGE


def test_freshness_box_covers_every_layer_and_ticks():
    """08-26: the chart carries a small per-layer freshness box under the ⓘ. Each layer
    prints the clock its data carries and a counter; the counter is a 1 s ticker that reads
    state only. Pinned: the mounter, the ticker on the poller lifecycle, the slow fetches'
    stamps, the chain's own clock on the model, and the info-modal entry."""
    assert "function snkMountFresh(" in PAGE and 'id="snk-fresh-lead"' in PAGE
    assert "SNDK.timers.fresh=setInterval(()=>SNDK.freshTick(), 1000)" in PAGE
    assert "clearInterval(SNDK.timers.fresh)" in PAGE                 # dies with the pollers
    for k in ("bars", "chain", "price", "read", "mirai", "path", "tape"):
        assert f"['{k}'," in PAGE                                      # one row per layer
    assert "SNDK.pathAt=Date.now()" in PAGE and "SNDK.tapeAt=Date.now()" in PAGE
    assert "bookAsof:(typeof meta.book_asof==='string')" in PAGE       # the chain's own stamp
    assert "['15','Freshness chip'," in PAGE                           # documented in the key
    assert "SNDK._freshEl=box" in PAGE and "getElementById('snk-tenors')" in PAGE   # lives in the tenors strip, node kept across rewrites
    assert "at least TWICE the whole book’s turnover" in PAGE          # the ring's exact rule, in the key
