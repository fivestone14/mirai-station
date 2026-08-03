"""mirai-voice :: jargon — fix what the ears mishear, after they hear it.

Parakeet has no vocabulary-biasing hook (none of the Mac ports do), and the
research verdict was that post-correction is the right layer anyway: shipping
Mac dictation tools tried recognition-time bias lists and retreated because
short bias lists polluted output. So this module is a deterministic sweep over
the transcript — regex first for known mis-hearings, then a conservative
fuzzy pass for long tokens only. Microseconds, no model, no hallucination.

Two tables:
  _RULES  — explicit (pattern → canonical) pairs, case-insensitive, word-bounded.
            Seeded from docs/gw-vocab.md terms + bench.py harvest; extend from
            state/voice/convo logs whenever a new mis-hearing shows up.
  _FUZZY  — glossary for rapidfuzz. Only tokens >= _FUZZY_MIN_LEN are compared
            (never "pin"/"pen" class words) and only at >= _FUZZY_CUTOFF.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz, process

# --- explicit mis-hearing rules ---------------------------------------------
# (pattern, replacement) — patterns compiled once, case-insensitive.
_RULES: list[tuple[str, str]] = [
    # instrument — "SNDK" is spoken as letters or as the company name
    (r"\bsan\s?disk\b", "SNDK"),
    (r"\bs\s?n\s?d\s?k\b", "SNDK"),
    (r"\bs\s?p\s?x\b", "SPX"),
    (r"\bvix\b", "VIX"),
    # greeks / exposures
    (r"\bjex\b|\bjecks\b|\bgecks\b|\bg\s?e\s?x\b", "GEX"),
    (r"\bdecks\b|\bdex\b|\bd\s?e\s?x\b", "DEX"),
    (r"\bvanna\b|\bvana\b", "vanna"),
    (r"\bcharm\b", "charm"),
    # levels & structure
    (r"\bh\s?v\s?l\b|\bhvl\b", "HVL"),
    (r"\bv\s?wap\b|\bvee\s?wap\b", "VWAP"),
    (r"\bmag\s?p\b|\bmagpie\b|\bmag\s?pea\b", "MagP"),
    # bench harvest 08-03: "Is Mag Double Prime still..." — bare "Mag" before a
    # prime phrase is MagP with the P swallowed
    (r"\bmag(?=\s+(?:double|triple)?\s*prime\b)", "MagP"),
    (r"\bg\s?w\s?c\b", "GWc"),
    (r"\bg\s?w\s?p\b", "GWp"),
    (r"\bgamma\s?flip\b", "gamma flip"),
    (r"\bcall\s?wall\b", "call wall"),
    (r"\bput\s?wall\b", "put wall"),
    # tenor — every spoken shape of 0DTE
    (r"\b(?:zero|oh|0)\s*d\.?\s*t\.?\s*e\.?\b", "0DTE"),
    # units
    (r"\bsigma\b", "sigma"),
    (r"\bexpected\s?move\b", "expected move"),
    # primes — "double prime" etc. arrive fine as words; normalize spacing
    (r"\btriple\s?prime\b", "triple prime"),
    (r"\bdouble\s?prime\b", "double prime"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _RULES]

# --- conservative fuzzy pass ------------------------------------------------
_FUZZY_GLOSSARY = [
    "gamma", "magnet", "dealer", "watchtower", "nightglass", "terrain",
    "regime", "momentum", "breach", "reclaim", "pinned", "sigma",
]
_FUZZY_MIN_LEN = 5
_FUZZY_CUTOFF = 88.0

_TOKEN_RE = re.compile(r"[A-Za-z]{%d,}" % _FUZZY_MIN_LEN)


def correct(text: str) -> tuple[str, list[str]]:
    """Return (corrected transcript, list of corrections applied)."""
    hits: list[str] = []
    for rx, repl in _COMPILED:
        text, n = rx.subn(repl, text)
        if n:
            hits.append(repl)

    def _fuzzy(m: re.Match) -> str:
        tok = m.group(0)
        low = tok.lower()
        if low in (g.lower() for g in _FUZZY_GLOSSARY):
            return tok
        best = process.extractOne(
            low, _FUZZY_GLOSSARY, scorer=fuzz.ratio, score_cutoff=_FUZZY_CUTOFF)
        if best:
            hits.append(best[0])
            return best[0]
        return tok

    text = _TOKEN_RE.sub(_fuzzy, text)
    return text, hits
