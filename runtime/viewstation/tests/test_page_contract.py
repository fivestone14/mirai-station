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
    assert "['11','Freshness chip'," in PAGE                           # documented in the key
    assert "SNDK._freshEl=box" in PAGE and "getElementById('snk-tenors')" in PAGE   # lives in the tenors strip, node kept across rewrites
    assert "hour12:true, timeZone:PT" in PAGE                        # AM/PM clocks are Pacific here — the page's rule
    assert "at least 2× what the whole book turns over" in PAGE       # the ring's exact rule, in the key
    assert '<svg class="snki-g"' in PAGE                              # every entry carries its glyph


def test_the_chain_row_is_judged_on_the_books_own_clock():
    """08-30: the chain row inherited `bars.stale`, which measures how old the
    SCAN is. The feed serves a disk cache on about half the scans, so a book
    2-4 minutes older than its own scan sat under a chip that said fresh because
    the scan was fresh — the layer with the longest lag was the one layer that
    could not report it. It carries its own stamp and its own ceiling now, and
    that ceiling is the reader's STALE_BOOK_MIN."""
    ft = PAGE.split("freshTick(){")[1].split("liveSpot(){")[0]
    assert "const bookAt=m?T(m.bookAsof):null;" in ft
    assert "const bookCap=(window.SNDK_STALE_BOOK_MIN||6)*60000;" in ft
    assert "chain: { at:bookAt, stale:bookAt!=null&&(now-bookAt)>bookCap }," in ft
    # Exactly one row may inherit staleInfo(), and it is `bars` — the row whose
    # clock staleInfo() actually measures. A second occurrence is the bug back.
    assert ft.count("stale:!!(fr&&fr.stale)") == 1


def test_the_oi_lanes_book_stamp_is_judged_on_the_books_own_clock():
    """08-30, the same fix in the second place it was wrong: the age printed
    at the head of the OI/volume lane took its colour from staleInfo(), which
    measures the SCAN clock. The book is never younger than the scan and lagged
    it by up to 3.35 min on the recorded tape, so there was a band where the
    FRESH chip called the book stale and the number printed right beside the
    bars that book drew stayed grey. The stamp reads meta.book_asof and applies
    the reader's own STALE_BOOK_MIN, exactly as the chip's chain row does.

    Sliced from the `const bkT` line so the paragraph above it — which names
    staleInfo() to explain what it replaced — cannot satisfy the last
    assertion."""
    assert "const bkT=(m&&m.bookAsof!=null)?+new Date(m.bookAsof):NaN;" in PAGE
    stamp = PAGE.split("const bkT=(m&&m.bookAsof!=null)?+new Date(m.bookAsof):NaN;")[1].split("// LINEAR LENGTH, SPLIT BY SIDE")[0]
    assert "const bkStale=(Date.now()-bkT)>((window.SNDK_STALE_BOOK_MIN||6)*60000);" in stamp
    assert "const fr={stale:bkStale};" in stamp          # the only fr in scope
    assert "staleInfo(" not in stamp                     # never the scan clock
    assert "?'--coral':'--ink-faint'" in stamp           # coral on the book's age
    assert 'data-name="Book age"' in stamp


def test_the_freshness_chip_leads_with_the_oldest_layer():
    """A chip that reports the freshest layer is reporting the layer that cannot
    be wrong. The lead line and the box colour both take the option book, which
    is the oldest thing on the board on any cached scan; bars is the fallback
    for a board carrying no book stamp yet, and neither one alone sets stale."""
    ft = PAGE.split("freshTick(){")[1].split("liveSpot(){")[0]
    assert "const stale=(!!rows.bars.stale&&rows.bars.at!=null)||(!!rows.chain.stale&&rows.chain.at!=null);" in ft
    assert "box.classList.toggle('stale', stale);" in ft
    assert "lead.textContent=rows.chain.at!=null?`book ${snkCount(now-rows.chain.at)}`" in ft
    assert ":(rows.bars.at!=null?`bars ${snkCount(now-rows.bars.at)}`:'awaiting scan');" in ft


def test_the_stale_floor_is_the_readers_own_ceiling():
    """08-30: the floor was a hardcoded 12 minutes, on the reasoning that a
    weekly-tenor scanner breathes slower than the SPX 0DTE loop. But the scan
    cadence is ~2 minutes, so 3*median is ~6 and the 12-minute floor was the
    binding constraint on every ordinary day — a scanner that died at 10:00 kept
    a live chip until 10:12.

    Six is not an arbitrary replacement for twelve. It is sndk_read's own
    STALE_BOOK_MIN, the age at which the reader itself stops spending a model
    call on the book, and PAYLOAD.render() lifts it off the payload's gate
    block — so the chip cannot quote a ceiling the reader has stopped enforcing.
    test_sndk_payload pins the same join from the server's end."""
    si = PAGE.split("staleInfo(){")[1].split("\n  },")[0]
    assert "const floor=(window.SNDK_STALE_BOOK_MIN||6)*60000;" in si
    assert "stale:age>Math.max(3*med, floor)" in si
    assert "720000" not in PAGE                  # the retired 12-minute floor
    assert "if(d.gates&&d.gates.stale_book_min!=null) window.SNDK_STALE_BOOK_MIN=d.gates.stale_book_min;" in PAGE


def test_the_payload_tab_says_the_clocks_before_the_json():
    """sr-7 (08-30): the scene's provenance is all inside the JSON, but the JSON
    is ~3,000 characters and nobody discounts a number for an age they had to go
    looking for. Three header lines answer it first — how old the quote is, how
    old the BOOK the structure came from is, and which night's open interest
    every wall and magnet rests on — and a block the freshness gate deleted gets
    a coral line of its own, because an absence is the one thing a JSON dump
    cannot show you.

    Run under JavaScriptCore against a real scene, an artificially staled one (a
    12-minute-old book behind a 1-minute-old scan: five blocks dropped, five
    coral lines) and a null scene (every field degrades to — or ?, none
    fabricated). These pin the pieces that carried it."""
    assert "+PAYLOAD.provLines(d.scene)" in PAGE      # rendered, not merely defined
    pl = PAGE.split("provLines(scene){")[1].split("\n  },")[0]
    assert "const ds=(scene&&scene.data_sources)||{}, fr=(scene&&scene.freshness_rules)||{};" in pl
    assert "const b=ds.options_book||{}, oi=ds.open_interest||{};" in pl
    assert "is_repeat_of_previous_scan===true?', REPEAT of the previous scan'" in pl
    # ...and the cadence line, which is the one a reader checks the other two
    # against: two counts and two intervals. Pinned by the call, so the 08-30
    # reshape that gave the counts their singular form could not have slipped
    # past this test the way it did the first time it was written.
    assert "${many(b.distinct_books_so_far_today,'distinct book')}" in pl
    assert "across ${many(ds.scans_so_far_today,'scan')} today" in pl
    # a deleted block is the one fact the dump below cannot carry, so it gets
    # its own line and its own colour
    assert "const dropped=fr.blocks_dropped_this_scan||[];" in pl
    assert "// DROPPED before the model saw it: " in pl
    assert "color:var(--coral,#e06c75)" in pl
    # and it builds innerHTML, so every free-text field goes through the escaper
    assert "const esc=x=>String(x).replace(/[&<>]/g," in pl
    for field in ("esc(ds.spot_feed||'?')", "esc(b.served_from||'?')",
                  "esc(x.block)", "esc(x.source)"):
        assert field in pl, field


def test_every_provlines_helper_escapes():
    """provLines builds innerHTML, and it once applied its escaper to ten fields
    and missed two: `t` (the clock) and `age` (the minutes) interpolated raw.
    Neither was reachable — both carry isoformat strings and rounded floats the
    local scanner produced, and `t` slices to eight characters besides — but
    reaching for an escaper and missing a path is exactly how the comment-form
    hole got into this page, so it was closed rather than argued about.

    It closed into a SHAPE, which is what makes it testable: every helper that
    turns a scene value into display text is now defined in terms of esc. This
    checks that structurally rather than by naming the three that exist today,
    so a FOURTH helper interpolating raw is a red test instead of a judgement
    call at review time — and the set assertion means adding one is a decision
    somebody has to make here, in writing.

    Verified by execution too, under JavaScriptCore: fed a scene with all
    fourteen string leaves carrying a different payload (<img onerror>,
    <script>, <svg onload>, <iframe>, <object>, <embed>), the raw un-stripped
    innerHTML contains only the two <span> tags provLines writes itself."""
    import re
    pl = PAGE.split("provLines(scene){")[1].split("\n  },")[0]
    helpers = dict(re.findall(r"^\s*const (\w+)\s*=\s*\(?[\w, ]*\)?\s*=>(.*)$", pl, re.M))
    assert set(helpers) == {"esc", "t", "age", "many"}, sorted(helpers)
    for name, body in helpers.items():
        if name == "esc":
            continue
        assert "esc(" in body, f"{name}() interpolates a scene value without esc()"
    # and esc itself still covers all three characters that open a tag or an entity
    assert "String(x).replace(/[&<>]/g," in helpers["esc"]


def test_the_read_pops_in_the_headers_air():
    """09-02: the latest reading also stands in the open as a translucent card in the header's
    one piece of measured air (below the mic card, out to the header's right edge, down to the
    voice column's bottom). Pinned: the node is a SIBLING of the repainted strips — never a child
    of #snk-vox, whose innerHTML paintDrawers rewrites — it is placed from live rects and
    re-placed when the chart resizes, it pops only on a new reading_ts, dismissal is remembered
    per reading, and its age is re-ticked in place on the spot poll like the caption's."""
    assert '<div class="snk-vox" id="snk-vox"></div>\n        <div class="snk-pop" id="snk-pop" hidden></div>' in PAGE
    assert "function snkPopBox(" in PAGE
    assert "paintPop(){" in PAGE and "placePop(){" in PAGE and "dismissPop(){" in PAGE
    assert "SNDK.paintPop();" in PAGE                                 # from paintDrawers, every paintSide
    assert "SNDK.buildChart(); SNDK.placePop();" in PAGE              # re-placed on resize
    assert "localStorage.setItem('snk.pop.x', key)" in PAGE           # dismissal is per reading
    assert "pop.dataset.since" in PAGE and "pop.classList.toggle('aged', pm>10)" in PAGE
    assert ".snk-pop{--pop-hue:var(--ink-faint); position:absolute" in PAGE and "backdrop-filter:blur(10px)" in PAGE
    assert ".snk-head{position:relative;" in PAGE                     # the card's containing block
    assert "openPop(on){" in PAGE and ".snk-pop.open{height:auto!important" in PAGE   # click = drawer-style overlay
    assert "el.classList.toggle('clip'" in PAGE and ".snk-pop.clip .pb{-webkit-mask-image" in PAGE   # at rest it fades, never scrolls
    assert 'class="lead"' in PAGE and "@keyframes snkPopGlow" in PAGE and "--pop-hue:var(--iris)" in PAGE   # round 2: leads, glows, wears its hue


def test_the_payload_tab_carries_the_schema_card():
    """09-02: a Schema button on the Payload tab opens one card drawing the whole SNDK-PRO
    pipeline — every file and what it does in plain words. The content is DATA so this test
    can pin the file names it cites against the tree, and the strip beside it no longer
    credits a vendor the SNDK chain never came from.

    09-04: redrawn. Six rows of identical boxes with one chevron between each pair said
    "everything above feeds everything below", which is not what happens. Components now
    declare a kind and every connection is its own line, so the data is _PL_NODES plus
    _PL_EDGES and this test pins that every edge endpoint actually resolves."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    assert 'id="pl-schema"' in PAGE and "function openPlSchemaModal(" in PAGE
    assert ">Pipeline Architecture</button>" in PAGE                 # renamed 09-02
    assert PAGE.index('id="pl-schema"') < PAGE.index('id="pl-seg"')    # ...and sits left of the segments
    # 09-04 redesign: the content is still DATA, now split into components and the
    # connections between them, so this test can pin both against the tree.
    assert "const _PL_NODES=[" in PAGE and "const _PL_EDGES=[" in PAGE
    assert ".modal-card.plsch{" in PAGE
    import re as _re
    _n0 = PAGE.index("const _PL_NODES=["); _n1 = PAGE.index("const _PL_EDGES=[")
    _nodes = _re.findall(r"^\s*\['([a-z0-9]+)',\s*'([^']+)',\s*'([a-z]+)'",
                         PAGE[_n0:_n1], _re.M)
    assert len(_nodes) >= 15, len(_nodes)
    # THE PROPERTY THE CARD NOW RESTS ON: kind and stage are one-to-one. That is
    # what let the legend go — the stage heading IS the definition, so a reader
    # does not learn a colour code before seeing anything to attach it to. If a
    # stage ever mixes kinds the heading starts lying and the legend has to
    # come back, so it is pinned here rather than left as an accident.
    _by_stage = {}
    for _id, _stage, _kind in _nodes:
        _by_stage.setdefault(_stage, set()).add(_kind)
    for _stage, _kinds in _by_stage.items():
        assert len(_kinds) == 1, (_stage, sorted(_kinds))
    # ...and every stage explains itself, in a line, where the reader meets it
    _d0 = PAGE.index("const _PL_STAGE_DOC={")
    _docs = set(_re.findall(r"'([A-Z][a-z]+)':", PAGE[_d0:PAGE.index("};", _d0)]))
    assert _docs == set(_by_stage), (sorted(_docs), sorted(_by_stage))
    # EVERY edge endpoint resolves to a component. A dangling id draws no line
    # and says nothing about it — the silent miss this build keeps hitting.
    _ids = {n[0] for n in _nodes}
    _edges = _re.findall(r"\['([a-z0-9]+)','([a-z0-9]+)'",
                         PAGE[_n1:PAGE.index("const _PL_STAGE_DOC={")])
    assert _edges, "no edges parsed"
    _flat = {x for pair in _edges for x in pair}
    assert _flat <= _ids, sorted(_flat - _ids)
    # the wiring shown in words is DERIVED from those same edges, never typed,
    # so the sentence and the line can never disagree
    assert "function _plWires(" in PAGE and "_PL_EDGES.filter(" in PAGE
    assert "Fed by" in PAGE and "Feeds" in PAGE
    # below this width the grid collapses, every card shares an x, and twenty
    # edges become one stroke — the words carry it there instead
    assert "const _PL_LINES_MIN_WIDTH=" in PAGE
    assert "window.innerWidth<_PL_LINES_MIN_WIDTH" in PAGE
    # Esc and the backdrop close the modal without touching the X, so the
    # resize listener and the observer have to come off in closeModal
    assert "function _plTeardown(" in PAGE
    assert PAGE.index("_plTeardown();") > PAGE.index("function closeModal(")  # called from it
    # the side packet is part of the picture, not a footnote to it
    assert "sndk_side.build_side()" in PAGE and "state/sndk_side" in PAGE
    for rel in ("skills/sndk-pro/sndk_hunter.py", "skills/sndk-pro/sndk_views.py",
                "skills/sndk-pro/sndk_bars.py", "skills/sndk-pro/sndk_read.py",
                "skills/sndk-pro/sndk_rag.py", "runtime/watch/intraday/sndk_deadman.py",
                "runtime/launchd/com.mirai-station.sndk-bars.plist"):
        assert (root / rel).exists(), rel
        assert rel.split("/")[-1].replace(".plist", "") in PAGE, rel
    assert "ThetaData supplies this week" not in PAGE       # the SNDK chain is the market-data server's
    assert "state/sndk_bars" in PAGE
    # 09-02 verification pass (three agents against the code): the packet is built every
    # scan, the guards are named
    assert "Every scan: the newest snapshot" in PAGE
    assert "forecast language is deleted before anyone sees it" in PAGE
    # ...the pager watches the scanner only, memory is not the reading model's to search
    assert "the scanner goes quiet (a silent reader is not yet watched)" in PAGE
    assert "the reading model has no tools and cannot reach it" in PAGE
    # 09-04: said in plain English now — the assertion is that the Diary card
    # states what a row actually holds, not that it uses the word "arrays"
    assert "eight numbers for every strike" in PAGE
    assert "ivb=n(g.iv_median_books,5)" in PAGE


def test_the_payload_tab_carries_the_diary_view():
    """09-02: a third segment shows the newest diary row beside the schema of that row —
    every key in plain words, checked live against the row (absent keys dim, arrays with
    their length, a dot on the keys the reader reads). The memory view stopped filing
    forecast-era slices as quiet: they are counted apart and drawn hatched."""
    assert 'data-view="diary"' in PAGE and 'id="pl-diary"' in PAGE
    assert "async diary(){" in PAGE and "paintDiary(day, total, row, sib){" in PAGE
    assert "const _DIARY_SCHEMA=[" in PAGE and "'gex_views.net_by_strike'" in PAGE
    assert "if(PAYLOAD.view!=='payload') return;" in PAGE          # the JSON pane is the payload view's
    assert "legacy(m){ return !!m && ('vector' in m) && !('quiet' in m); }" in PAGE
    assert "seg('l',lg)" in PAGE and ".mem-days .rhy i.l{" in PAGE
    assert "['Since last read', esc(m.frame_is)]" in PAGE


def test_sndk_pro_is_no_longer_labelled_beta():
    """09-02: the SNDK tab left beta. No beta pill in the header, no beta tag on the rail;
    the `snk-beta` class survives only as the small tag style the Payload rail item borrows."""
    assert 'SNDK<span class="snk-beta">beta</span>' not in PAGE
    assert "β BETA" not in PAGE and "is a beta surface" not in PAGE
    assert 'data-tab="payload"' in PAGE


def test_the_rail_is_two_instrument_groups():
    """09-02: eight flat tabs read as one list and it was hard to tell SPX from SNDK. The rail
    is two groups with a header each; the group holding the active tab is always open, a
    header click opens the other and shows its last tab. Every tab still exists, inside its
    group."""
    assert 'data-grp="spx"' in PAGE and 'data-grp="sndk"' in PAGE
    assert PAGE.count('class="grp-hd"') == 2
    assert "function setRailGroup(" in PAGE and "function syncRailGroups(" in PAGE
    assert "setRailGroup(g.dataset.grp, !!on);" in PAGE          # an accordion: one group open, the active one
    assert "\\u203a" not in PAGE                            # the chevron is the character, not an escape
    for tab in ("map", "layers", "diary", "heads", "replay", "dict", "sndk", "payload"):
        assert f'data-tab="{tab}"' in PAGE, tab
    spx = PAGE.index('data-grp="spx"'); sndk = PAGE.index('data-grp="sndk"')
    assert spx < PAGE.index('data-tab="dict"') < sndk < PAGE.index('data-tab="payload"')
    assert "SPX·0DTE</span>" not in PAGE                 # the footer glyph no longer names one instrument


def test_the_station_opens_on_sndk_for_now():
    """09-02, TEMPORARY by the user's word: the page boots into the SNDK map. One constant
    carries it so restoring the SPX default is a one-word change."""
    assert "const DEFAULT_TAB='sndk';" in PAGE
    assert "if(DEFAULT_TAB!=='map') switchTab(DEFAULT_TAB);" in PAGE
