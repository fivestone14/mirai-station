"""Mirai Viewstation — pipeline manifest builder (gex-only system).

Produces ONE structured map of the whole system as a friendly pipeline —
five stages (Senses → Brain → Record → Memory → Tell), each with its real
modules translated into plain English (the gravity/flow vocabulary), and
pointers to the data each one stores so the tablet can open a formatted view.

Post 2026-07-03 restructure: the nine-voter/bet-watching stages are gone; the
Fade Lens is the brain and everything stays paper until the report cards prove
it. Pure stdlib, reads only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[2]          # .../mirai-station
SKILL_DIR = PLUGIN_ROOT / "skills" / "mirai-left-eye"
STATE_DIR = PLUGIN_ROOT / "state"
CONFIG_DIR = PLUGIN_ROOT / "runtime" / "watch" / "config"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


# --- small file helpers -------------------------------------------------------
def _latest_rel(glob: str, root: Path = STATE_DIR) -> Optional[str]:
    """Newest file matching glob, as a path relative to `root` (for /api/raw/file)."""
    try:
        files = sorted(root.glob(glob))
        return str(files[-1].relative_to(root)) if files else None
    except Exception:
        return None


def _file_ref(label: str, root: str, rel: Optional[str], fmt: str, note: str = "") -> Optional[dict]:
    if not rel:
        return None
    return {"kind": "file", "label": label, "root": root, "path": rel, "format": fmt, "note": note}


def _compact(refs: list) -> list:
    return [r for r in refs if r]


# --- the pipeline stages ------------------------------------------------------
def build_pipeline() -> dict:
    stages = [
        {
            "id": "senses", "emoji": "🛰️", "title": "The Senses", "tag": "gather the data",
            "what": "Pulls in everything the gravity read needs — live prices, real option "
                    "chains, who's buying vs selling, and the overnight news read.",
            "tech": "lefteye_fetcher · native_gex_feed · lefteye_fill_ledger · macro_mood",
            "modules": [
                {"emoji": "📡", "name": "lefteye_fetcher", "title": "The Data Runner",
                 "what": "Fetches raw market data on request — price bars, quotes, option chains, VIX.",
                 "data": []},
                {"emoji": "🛰️", "name": "native_gex_feed", "title": "Real-Data Read + Flow Sensor",
                 "what": "One time-boxed trip to ThetaData per scan: the actual SPX option book, "
                         "plus who's hitting the buy vs sell button right now (the flow that can overpower gravity).",
                 "data": []},
                {"emoji": "🧾", "name": "lefteye_fill_ledger", "title": "The Fill Ledger",
                 "what": "Tracks where options orders are filling RIGHT NOW (45-minute fade), so the "
                         "magnet follows fresh money instead of the morning's frozen totals.",
                 "data": _compact([_file_ref("today's fill weights", "state",
                                             _latest_rel("gex_fills/*.json"), "kv")])},
                {"emoji": "🌙", "name": "macro_mood", "title": "The Morning Mood",
                 "what": "Before the open it reads the overnight tape and sets an expected lean for "
                         "the day. Shadow only — it tilts context, never trades.",
                 "data": _compact([
                     _file_ref("today's mood read", "state", "market_expectation/latest.json", "mood"),
                     _file_ref("mood vs. reality (learning)", "state",
                               _latest_rel("market_expectation/learning-*.jsonl"), "events"),
                 ])},
                {"emoji": "🧭", "name": "right_eye", "title": "The Drift Gauge",
                 "what": "Embeds the morning macro read into a vector so the day's tape can be measured "
                         "against it (drift). The news-memory store was retired 2026-07-10 — embedder only now.",
                 "data": [{"kind": "info", "label": "macro embedding (bge-small · 384-d)",
                           "note": "Feeds macro drift_cosine; no browsable store."}]},
            ],
        },
        {
            "id": "brain", "emoji": "🧠", "title": "The Brain", "tag": "the Fade Lens decides",
            "what": "The GRAVITY ENGINE maps where dealer positioning pulls price; the Fade Lens "
                    "asks four questions — stretched? gravity favors a snap-back? room to run? "
                    "did it turn? — and only then makes a paper bet.",
            "tech": "lefteye_gex_box · reversion_lens · resolve_dealer_map",
            "modules": [
                {"emoji": "🧲", "name": "lefteye_gex_box", "title": "The Gravity Engine",
                 "what": "Six slides from the option book: the magnet (fill-ledger fed), the walls, "
                         "the flip line, the regime — cross-checked against live FLOW (a pin fighting "
                         "one-way tape is downgraded to 🟡 uncertain).",
                 "data": []},
                {"emoji": "🎚️", "name": "reversion_lens", "title": "The Fade Lens (the brain)",
                 "what": "Watches SPX for stretched moves with room to snap back to the magnet — "
                         "paper-only until the report cards prove it.",
                 "data": _compact([_file_ref("today's scans (the diary)", "state",
                                             _latest_rel("reversion/*.jsonl"), "reversion")])},
                {"emoji": "🗺️", "name": "resolve_dealer_map", "title": "The Dealer Map (one story)",
                 "what": "Collapses the two engine reads into ONE displayed truth: proxy leads, the "
                         "native read shows as an agree/disagree badge.",
                 "data": []},
            ],
        },
        {
            "id": "record", "emoji": "📓", "title": "The Record", "tag": "every scan on paper",
            "what": "Every 5 minutes the Shift Manager runs the scan and writes the evidence down — "
                    "nothing is remembered by vibes, everything by the diary.",
            "tech": "hunter · reversion_lens.record",
            "modules": [
                {"emoji": "⏱️", "name": "hunter", "title": "The Shift Manager",
                 "what": "The every-5-minutes worker: wakes, runs the scan on each index, records the "
                         "results, flags fresh paper fires, sleeps.",
                 "data": _compact([_file_ref("today's tick heartbeats", "skill_logs",
                                             _latest_rel("*.jsonl", SKILL_DIR / "logs"), "events")])},
                {"emoji": "📓", "name": "reversion_lens.record", "title": "The Diary Writer",
                 "what": "Appends both gravity snapshots + the lens's decision trail to the permanent "
                         "evidence stream, one row per scan.",
                 "data": _compact([_file_ref("the diary (today)", "state",
                                             _latest_rel("reversion/*.jsonl"), "reversion")])},
            ],
        },
        {
            "id": "memory", "emoji": "📚", "title": "The Memory", "tag": "learn & prove",
            "what": "Every night the report cards grade both engines against what price actually did; "
                    "nothing gets promoted until the record rules out luck (the Wilson gate).",
            "tech": "gex_polarity_ab · macro_mood reliability",
            "modules": [
                {"emoji": "📊", "name": "gex_polarity_ab", "title": "The Report Cards",
                 "what": "After each close: did each engine's calls match the tape? Grades per IDEA "
                         "(episodes, not clock ticks) + measures how close price came to each magnet.",
                 "data": _compact([
                     _file_ref("latest report card", "state",
                               _latest_rel("reversion/polarity-*.json"), "generic"),
                     _file_ref("the accumulating ledger", "state",
                               "reversion/polarity_history.jsonl", "events"),
                 ])},
                {"emoji": "🌡️", "name": "mood reliability", "title": "The Mood's Track Record",
                 "what": "Each close, the morning mood's direction call is scored; its influence "
                         "grows only as its record earns it.",
                 "data": _compact([_file_ref("mood reliability", "state",
                                             "market_expectation/reliability.json", "kv")])},
            ],
        },
        {
            "id": "tell", "emoji": "📣", "title": "The Tell", "tag": "what you see & hear",
            "what": "The storytellers render one dealer story onto the tablet and the Obsidian notes; "
                    "the alert bell rings your phone only for fresh paper fires and broken walls.",
            "tech": "snapshot · app.js · dashboard · gex_alerts · push_ntfy",
            "modules": [
                {"emoji": "📱", "name": "snapshot · app.js", "title": "The Storytellers (tablet)",
                 "what": "The gravity map leads the Overview; every level comes from the one resolved "
                         "dealer map, so the tablet and the notes can never disagree.",
                 "data": []},
                {"emoji": "📝", "name": "dashboard · learning view", "title": "The Storytellers (notes)",
                 "what": "The plain-English 'Today' note and the ULT learning note in Obsidian.",
                 "data": []},
                {"emoji": "🔔", "name": "gex_alerts · push_ntfy", "title": "The Alert Bell",
                 "what": "Pushes fresh paper fires to the phone, re-fires the mood analyst when a "
                         "dealer wall breaks, and scores the mood at the close.",
                 "data": _compact([_file_ref("today's pushes", "state",
                                             _latest_rel("logs/watch-pushes-*.jsonl"), "events")])},
                {"emoji": "🔐", "name": "auth · token_watcher", "title": "The Caretaker",
                 "what": "Keeps the broker login alive and pings the phone before it expires.",
                 "data": []},
            ],
        },
    ]

    return {"stages": stages}


if __name__ == "__main__":
    print(json.dumps(build_pipeline(), indent=2, default=str))
