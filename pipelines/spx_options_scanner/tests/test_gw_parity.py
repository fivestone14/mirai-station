"""test_gw_parity — automated twin-parity guard for the GW notation standard.

There are TWO implementations of the GW vocab: the Python source of truth
(skills/mirai-left-eye/gw_vocab.py) and its JS twin, the `const GW = {…}` block
embedded in runtime/viewstation/static/index.html. The standard (docs/gw-vocab.md)
requires the two stay in step; before this test the only guard was manual
discipline + inline "# JS-twin parity" comments, so a threshold changed in one
twin but not the other would drift silently until it mis-rendered a live wall.

This harness reads the JS block as text and asserts every load-bearing constant
matches the Python module. It does NOT execute the JS (no node / JS engine in the
runtime) — so it guards THRESHOLD drift (the common, dangerous case: someone edits
a cut in one place), not a deep logic rewrite. If a JS engine is ever vendored,
add a behavioural fixtures pass beside this one. Keep BOTH twins changed together
and rerun this file.

KNOWN NOT-COVERED divergences (a green run does NOT prove these agree — a future
behavioural pass should) — don't over-trust the check:
  - nearestWalls sort direction (above asc / below desc) and the >/<= spot split.
  - infer_step's first-seen modal tie-break (mirrors Counter.most_common(1)).
  - pinStatus nearest-peak tie-break and the span ±step boundary (both twins 1e-9).
  - share rounding: Python round(share,4) vs JS unrounded (display-only).
  - primeTier null/NaN guard: JS returns "'"; Python prime_tier(None) raises.
  - the gap-tolerance epsilon: JS 2*step+1e-6 vs Python 2*step+_EPS (1e-9) —
    behaviourally immaterial on a real grid, deliberately not asserted.

Caveat: _gw_block() balances braces over the raw text (strings/comments included).
Correct for today's block; a stray unbalanced brace in a future JS comment/regex
literal could truncate the slice and cause confusing failures — keep the block clean.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gw_vocab as gw  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.normpath(os.path.join(
    _HERE, "..", "..", "..", "runtime", "viewstation", "static", "index.html"))


def _gw_block():
    """The `const GW = {…}` twin, sliced from index.html by brace balance."""
    if not os.path.exists(_INDEX):
        pytest.skip(f"viewstation index.html not found at {_INDEX}")
    with open(_INDEX, encoding="utf-8") as fh:
        src = fh.read()
    start = src.find("const GW = {")
    assert start != -1, "the JS twin `const GW = {` was not found in index.html"
    i = src.find("{", start)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    raise AssertionError("unbalanced braces in the GW block")


def _nums(pattern, text):
    """Every float captured by `pattern` (single capture group) in `text`."""
    return [float(x) for x in re.findall(pattern, text)]


BLOCK = _gw_block()


# ------------------------------------------------------------------ thresholds
def test_prime_tier_cuts_match():
    # primeTier: share>=0.60 ? "'''" : share>=0.25 ? "''" : "'"
    m = re.search(r"share\s*>=\s*([0-9.]+)\s*\?\s*\"'''\"\s*:\s*"
                  r"share\s*>=\s*([0-9.]+)\s*\?\s*\"''\"", BLOCK)
    assert m, "could not find the primeTier ternary in the JS twin"
    dominant, significant = float(m.group(1)), float(m.group(2))
    assert dominant == gw.PRIME_DOMINANT, (dominant, gw.PRIME_DOMINANT)
    assert significant == gw.PRIME_SIGNIFICANT, (significant, gw.PRIME_SIGNIFICANT)


def test_hysteresis_matches():
    # tierStable: tn(s, 0.25+0.07, 0.60+0.07) and tn(s, 0.25-0.07, 0.60-0.07)
    ups = re.search(r"tn\(s,\s*([0-9.]+)\s*\+\s*([0-9.]+),\s*"
                    r"([0-9.]+)\s*\+\s*([0-9.]+)\)", BLOCK)
    downs = re.search(r"tn\(s,\s*([0-9.]+)\s*-\s*([0-9.]+),\s*"
                      r"([0-9.]+)\s*-\s*([0-9.]+)\)", BLOCK)
    assert ups and downs, "could not find the tierStable hysteresis cuts"
    sig_up, hy1, dom_up, hy2 = map(float, ups.groups())
    sig_dn, hy3, dom_dn, hy4 = map(float, downs.groups())
    # the ±0.07 band is identical on every cut, up and down
    for hy in (hy1, hy2, hy3, hy4):
        assert hy == gw.TIER_HYSTERESIS, (hy, gw.TIER_HYSTERESIS)
    # and the cut ladder itself is the prime-tier ladder
    assert sig_up == sig_dn == gw.PRIME_SIGNIFICANT
    assert dom_up == dom_dn == gw.PRIME_DOMINANT


def test_strike_floor_matches_with_epsilon():
    # clusterWalls concentration floor: Math.abs(p[1])>=0.25*kmx-1e-9
    m = re.search(r"Math\.abs\(p\[1\]\)\s*>=\s*([0-9.]+)\s*\*\s*kmx\s*-\s*([0-9e.+-]+)",
                  BLOCK)
    assert m, "could not find the concentration-floor filter in the JS twin"
    floor, eps = float(m.group(1)), float(m.group(2))
    assert floor == gw.STRIKE_FLOOR, (floor, gw.STRIKE_FLOOR)
    # the −1e-9 twin-parity tolerance (mirror of gw_vocab floor − _EPS): a strike
    # sitting exactly on the floor must survive in BOTH twins, not just Python.
    assert eps == gw._EPS, (eps, gw._EPS)


def test_noise_floor_matches():
    # clusterWalls drop: c.strength>=0.05*mx
    m = re.search(r"c\.strength\s*>=\s*([0-9.]+)\s*\*\s*mx", BLOCK)
    assert m, "could not find the noise-floor drop in the JS twin"
    assert float(m.group(1)) == gw.NOISE_FLOOR, (m.group(1), gw.NOISE_FLOOR)


def test_gap_tolerance_matches():
    # same-sign adjacency: (k-cur.hi)<=2*step  (≤1 missing step stays one wall)
    m = re.search(r"\(k\s*-\s*cur\.hi\)\s*<=\s*([0-9]+)\s*\*\s*step", BLOCK)
    assert m, "could not find the gap-tolerance rule in the JS twin"
    assert int(m.group(1)) == 2, "the ≤1-missing-step (2*step) adjacency drifted"


def test_tier_marks_match():
    # tierStable marks array: ["'","''","'''"]
    m = re.search(r"marks\s*=\s*\[\s*\"'\"\s*,\s*\"''\"\s*,\s*\"'''\"\s*\]", BLOCK)
    assert m, "the JS tier-mark ladder does not match the ' '' ''' standard"
    assert gw._TIER_MARKS == ("'", "''", "'''")


def test_magp_and_pin_default_tier():
    # magpTier off-wall (null-magnet) default. Capture the literal returned from
    # INSIDE the magpTier body — NOT a bare `'return "''"' in BLOCK`, which any
    # other return-"''" line in the block would satisfy (false-pass) — and compare
    # to gw_vocab.magp_tier's None→default path.
    m = re.search(r"if\(magnet==null\|\|!isFinite\(magnet\)\)\s*return\s*\"([^\"]*)\"",
                  BLOCK)
    assert m, "could not find magpTier's null-magnet default return in the JS twin"
    assert m.group(1) == gw.magp_tier(None, [], 1.0), (m.group(1), gw.magp_tier(None, [], 1.0))


def test_gwlabel_format_matches():
    # gwLabel: (tenor?`[${tenor}]`:'')+'GW'+s+(tier||'')  → "[0DTE]GWc'''".
    # Assert the tenor-BRACKET format and the side-letter mapping too, not just the
    # 'GW'+s core — a `[${tenor}]`→`(${tenor})` or a flipped c/p would else false-pass.
    assert re.search(r"tenor\s*\?\s*`\[\$\{tenor\}\]`\s*:\s*''", BLOCK), "tenor bracket format changed"
    assert re.search(r"side===\s*'c'\s*\|\|\s*side===\s*'call'\s*\)\s*\?\s*'c'\s*:\s*'p'", BLOCK), \
        "gwLabel side-letter mapping changed"
    assert re.search(r"'GW'\s*\+\s*s\s*\+\s*\(tier", BLOCK), "gwLabel core shape changed"
    assert gw.gw_label("call", "'''", "0DTE") == "[0DTE]GWc'''"
    assert gw.gw_label("put", "", None) == "GWp"
