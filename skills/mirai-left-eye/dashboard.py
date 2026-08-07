"""
dashboard.py — render the human-facing "Mirai Awakening — Today" tracker.

A single, plain-English file the user opens in their Obsidian vault. It is the
at-a-glance INTERFACE to what the system is doing right now — written so anyone
(no markets/code knowledge) can read it. Everything technical is translated to
a kid-simple idea; single-sentence bullets; no jargon.

It is rebuilt from live state every time hunter.py scans. Gex-only since the
2026-07-03 restructure (the nine-voter/real-pick system was retired — the Fade
Lens is the brain, and everything stays paper until the report cards prove it):

    * One-breath summary  <- the whole day in a sentence
    * Right now           <- what the lens sees, and the day's mood
    * Practice corner     <- the SPX "fade-the-bounce" paper guesses
                             (reuses reversion_lens' OWN resolved numbers, so the
                             Today view and the Learning note can never disagree)
    * What it's thinking  <- the morning macro mood-read, in plain words
    * What it learned today <- one plain takeaway from the practice guesses
    * How the day unfolded <- a light, data-derived timeline
    * Watcher list        <- (collapsed) what the gex system watches for

Reset behavior: the file PERSISTS the day's session after the 16:00 ET close
(and holds the most recent session across nights/weekends), and blanks to a
fresh slate ONLY at pre-market.

This module only READS state (never mutates it) and writes one .md file; any
failure is swallowed by the caller (and per-section) so it can never break a
scan.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SKILL_DIR / "config.json"

# State dir (where the macro-mood + reversion telemetry live). Mirrors paths.py:
# env override first, else $PLUGIN_ROOT/state ( = skills/mirai-left-eye/../../state ).
STATE_DIR = Path(
    os.environ.get("MIRAI_STATION_STATE") or (SKILL_DIR.parent.parent / "state")
).expanduser()

# Default render target — the user's Obsidian vault root. Overridable via
# $MIRAI_STATION_DASHBOARD, else config.json -> {"dashboard_path": "/abs/path/to/file.md"}.
DEFAULT_VAULT_FILE = (
    "~/Documents/Obsidian Vault/Mirai Awakening — Today.md"
)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# ---------------------------------------------------------------------------
# Watcher metadata — what the gex system watches, in plain English. Used by the
# collapsed watcher list at the foot of the note (and mirrored on the tablet).
# ---------------------------------------------------------------------------
GEX_WATCHERS: list[dict[str, str]] = [
    {"code": "Gravity map",
     "what": "Where dealer positioning pulls price — the magnet, the walls, the flip line."},
    {"code": "Fill ledger",
     "what": "Where options orders are filling right now, so the magnet follows fresh money."},
    {"code": "Flow sensor",
     "what": "Who's hitting the buy vs sell button on today's options — the shove that can overpower gravity."},
    {"code": "Stretch gauge",
     "what": "How far price has been yanked from fair value (the day's average traded price)."},
    {"code": "Runway check",
     "what": "Whether there's enough room to the magnet for a snap-back to be worth it."},
    {"code": "Morning mood",
     "what": "The pre-open news read — upbeat or downbeat, and how sure."},
    {"code": "Wall alarm",
     "what": "Rings when price breaks concretely through a dealer wall — the map may be redrawn."},
]


# ---------------------------------------------------------------------------
# Small read/format helpers
# ---------------------------------------------------------------------------


def _vault_file() -> Path:
    env = os.environ.get("MIRAI_STATION_DASHBOARD")
    if env:
        return Path(env).expanduser()
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        if isinstance(cfg.get("dashboard_path"), str):
            return Path(cfg["dashboard_path"]).expanduser()
    except (OSError, json.JSONDecodeError):
        pass
    return Path(DEFAULT_VAULT_FILE).expanduser()


# ---------------------------------------------------------------------------
# Plain-English word maps
# ---------------------------------------------------------------------------
_SECTOR_PLAIN = {
    "semis": "chip makers", "tech": "tech companies", "energy": "energy",
    "rates": "interest-rate-sensitive stocks", "financials": "banks",
    "health": "health care",
}


def _sector_plain(key: str) -> str:
    return _SECTOR_PLAIN.get(key, key.replace("_", " "))


def _mood_words(d: float) -> str:
    if d >= 0.6:
        return "feeling upbeat (expecting the market up)"
    if d >= 0.2:
        return "mildly hopeful (leaning up)"
    if d > -0.2:
        return "undecided (roughly flat)"
    if d > -0.6:
        return "a little cautious (leaning down)"
    return "downbeat (expecting the market down)"


def _conf_words(c: float) -> str:
    if c >= 0.66:
        return "and quite sure"
    if c >= 0.4:
        return "but only moderately sure"
    return "though not very sure"


# ---------------------------------------------------------------------------
# Live-state readers (each is best-effort; returns None / [] on any failure)
# ---------------------------------------------------------------------------
def _macro_mood(now: datetime) -> Optional[dict]:
    """Today's morning macro mood-read, or None if it hasn't run today."""
    try:
        data = json.loads(
            (STATE_DIR / "market_expectation" / "latest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("date") == now.date().isoformat() else None


def _reversion_summary(now: datetime) -> Optional[dict]:
    """Today's SPX shadow-lens scorecard, computed by reversion_lens itself
    (single source of truth — the Today view and the Learning note share it)."""
    try:
        import reversion_lens as rl  # lazy: avoids a circular import at load
        rows = rl._read_today_telemetry(now)
        if not rows:
            return None
        a = rl._aggregate(rows, "SPX")
        resolved, pending = rl._resolve_beta_trades(rows, fire_key="fired")
        wins = sum(1 for r in resolved if r["outcome"] == "win")
        losses = sum(1 for r in resolved if r["outcome"] == "loss")
        scratch = sum(1 for r in resolved if r["outcome"] == "scratch")
        return {
            "scans": a["scans"],
            "armed": a["armed"],
            # distinct de-duped guesses (a setup that stays armed for many ticks
            # counts ONCE) = the entries _resolve_beta_trades actually graded.
            "guesses": len(resolved) + pending,
            "resolved": resolved, "pending": pending,
            "wins": wins, "losses": losses, "scratch": scratch,
            "spx_status": rl._prospect_status(a["latest"])[0],
        }
    except Exception:  # noqa: BLE001 — the dashboard must never break a scan
        return None


# ---------------------------------------------------------------------------
# Section renderers — every one returns a self-contained block of plain English
# ---------------------------------------------------------------------------
def _one_breath(rev: Optional[dict]) -> str:
    g = rev["guesses"] if rev else 0
    decided = (rev["wins"] + rev["losses"]) if rev else 0
    parts = ["Mirai watched the market all day"]
    if g and decided:
        parts.append(f"made **{g} practice guesses** ({rev['wins']} of {decided} right so far)")
    elif g:
        parts.append(f"made **{g} practice guesses**")
    parts.append("and placed **zero real trades** — the whole system is in practice "
                 "mode while the new gravity engine earns trust on its report cards")
    return ("> [!info] The whole day in one breath\n> " + ", ".join(parts) + ".")


def _right_now_section(rev: Optional[dict], mood: Optional[dict]) -> str:
    if rev:
        lens = f"- **The lens right now:** SPX is **{rev['spx_status']}**."
    else:
        lens = "- **The lens right now:** no readings yet today."
    real = ("- **Real trades:** none — everything is paper until the nightly "
            "report cards prove the new engine (then it earns a live seat).")
    if mood:
        o = mood.get("overall", {})
        mood_line = (f"- **Its mood:** {_mood_words(float(o.get('direction', 0) or 0))}, "
                     f"{_conf_words(float(o.get('confidence', 0) or 0))}.")
    else:
        mood_line = "- **Its mood:** no morning read yet today."
    return "## 🚦 Right now\n\n" + "\n".join([lens, real, mood_line])


def _practice_corner_section(rev: Optional[dict]) -> str:
    head = "## 🧪 Practice corner  ·  _pretend money only_"
    if not rev or rev["scans"] == 0:
        return f"{head}\n\n_No practice readings taken yet today._"
    decided = rev["wins"] + rev["losses"]
    g = rev["guesses"]
    lines = [head, "",
             f"- It studied SPX **{rev['scans']} times** today and made "
             f"**{g}** practice bounce-{'guess' if g == 1 else 'guesses'}."]
    if decided:
        lines.append(f"- Of the guesses that finished, it got **{rev['wins']} of "
                     f"{decided} right** — each playing out in minutes.")
    elif g:
        lines.append(f"- Its guesses are **still playing out** ({rev['pending']} not finished yet).")
    else:
        lines.append("- Nothing stretched far enough to make a guess — it's on stand-by.")
    if rev["resolved"]:
        lines += ["", "| Time | Guess | Did it work? |", "|:--|:--|:--|"]
        glyph = {"win": "✅ yes", "loss": "❌ no", "scratch": "➖ no clear result"}
        for r in sorted(rev["resolved"], key=lambda x: x["ts"])[-6:]:
            side = "would bounce up" if r["dir"] == "call" else "would bounce down"
            lines.append(f"| {r['ts'][11:16]} | {r['ticker']} {side} "
                         f"| {glyph.get(r['outcome'], r['outcome'])} |")
    lines += ["", "_These are paper guesses only — no real money is ever placed here._"]
    return "\n".join(lines)


def _thinking_section(mood: Optional[dict]) -> str:
    head = "## 🧠 What it's thinking"
    if not mood:
        return f"{head}\n\n_No morning mood-read yet today._"
    o = mood.get("overall", {})
    d = float(o.get("direction", 0) or 0)
    lines = [head, "",
             f"- This morning's read: **{_mood_words(d)}**, "
             f"{_conf_words(float(o.get('confidence', 0) or 0))} — so it's watching, not betting."]
    sectors = mood.get("sectors", {}) or {}
    if sectors:
        top = max(sectors.items(), key=lambda kv: kv[1].get("direction", 0) or 0)
        bot = min(sectors.items(), key=lambda kv: kv[1].get("direction", 0) or 0)
        if (top[1].get("direction", 0) or 0) > 0:
            tail = (f" and most downbeat on **{_sector_plain(bot[0])}**"
                    if (bot[1].get("direction", 0) or 0) < 0 else "")
            lines.append(f"- It's most hopeful about **{_sector_plain(top[0])}**{tail}.")
    lines.append("- This morning mood-reader is **new and hasn't earned trust yet**, "
                 "so it can only watch — it isn't allowed to place real trades.")
    return "\n".join(lines)


def _learned_section(rev: Optional[dict]) -> str:
    head = "## 🎓 What it learned today"
    if not rev or rev["scans"] == 0:
        return f"{head}\n\n_Nothing yet — it hasn't taken readings today._"
    decided = rev["wins"] + rev["losses"]
    if decided:
        body = [f"- Its **\"only bet on a bounce when there's room to run\"** rule held up: "
                f"**{rev['wins']} of {decided}** practice guesses worked, each bouncing the "
                f"small amount it hoped for within minutes.",
                "- Lesson reinforced: **wait for room** before betting on a snap-back."]
    else:
        body = ["- A quiet day — not enough finished guesses to learn much yet; "
                "it mostly practiced spotting when *not* to bet."]
    return head + "\n\n" + "\n".join(body)


def _timeline_section(mood: Optional[dict], rev: Optional[dict],
                      now: datetime) -> str:
    head = "## ⏱️ How the day unfolded"
    items: list[str] = []
    if mood:
        d = float(mood.get("overall", {}).get("direction", 0) or 0)
        items.append(f"- **Morning:** read the overnight news and felt **{_mood_words(d)}**.")
    if rev and rev["resolved"]:
        glyph = {"win": "✅ it was right", "loss": "❌ it was wrong",
                 "scratch": "➖ no clear result"}
        for r in sorted(rev["resolved"], key=lambda x: x["ts"]):
            side = "up" if r["dir"] == "call" else "down"
            items.append(f"- **{r['ts'][11:16]}:** practice guess — {r['ticker']} would "
                         f"bounce {side} → {glyph.get(r['outcome'], r['outcome'])}.")
    elif rev and rev["guesses"]:
        items.append(f"- **Late morning:** made {rev['guesses']} practice "
                     "bounce-guesses (still being graded).")
    if _market_phase(now) == "open":
        if rev and "stand by" in rev["spx_status"]:
            tail = "the market's calm and nothing's stretched — SPX is on stand-by."
        elif rev:
            tail = f"SPX is **{rev['spx_status']}**."
        else:
            tail = "watching the market."
        items.append(f"- **Now ({now.strftime('%H:%M')} ET):** {tail}")
    else:
        items.append("- **Now:** the market is closed for the day.")
    if not items:
        return f"{head}\n\n_Nothing has happened yet today._"
    return head + "\n\n" + "\n".join(items)


def _watcher_list_section() -> str:
    lines = ["<details><summary>🗂️ What the gex system watches for</summary>", "",
             "| Watcher | What it looks for |", "|:--|:--|"]
    for m in GEX_WATCHERS:
        lines.append(f"| **{m['code']}** | {m['what']} |")
    lines += ["", "</details>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Day-phase + framing
# ---------------------------------------------------------------------------
def _market_phase(now: datetime) -> str:
    """open | premarket | closed — drives live render vs blank template."""
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if t < time(9, 30):
        return "premarket"
    if t >= time(16, 0):
        return "closed"
    return "open"


FRONTMATTER = "---\ncssclasses:\n  - mirai\n---"
TITLE = "# 🧠 Mirai Awakening — Today"


def _context_line(now: datetime, note: str) -> str:
    return (f"_{WEEKDAYS[now.weekday()]} {now.date().isoformat()} · "
            f"{now.strftime('%H:%M')} ET · {note}_")


def _blank_template(now: datetime) -> str:
    """The fresh-reset slate — only ever rendered at PRE-MARKET."""
    return (
        f"{FRONTMATTER}\n\n{TITLE}\n\n"
        f"{_context_line(now, 'pre-market — fresh slate, waiting for the 9:30 ET open')}\n\n"
        "---\n\n"
        "> [!info] The whole day in one breath\n"
        "> A brand-new day — nothing has happened yet; Mirai is waiting for the "
        "market to open at 9:30 ET.\n\n"
        "---\n"
        "_Resets each morning before the open · practice guesses are paper-only · "
        "not financial advice._\n"
    )


def _footer() -> str:
    return ("_Built from live state · plain-English view · resets each premarket · "
            "not financial advice._")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _latest_session_date(now: datetime) -> str | None:
    """Most recent date (≤ today) with a lens diary — so the closed view
    persists the LAST real session until the next premarket reset."""
    today = now.date().isoformat()
    dates = [p.stem for p in (STATE_DIR / "reversion").glob(
        "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl") if p.stem <= today]
    return max(dates) if dates else None


def render(now: datetime, date_iso: str | None = None) -> str:
    """Full Markdown for the dashboard at `now` (pure-ish; reads live state).

    Reset model: the ONLY fresh wipe is PRE-MARKET. After the close the file
    PERSISTS the session (and holds it overnight/weekends) until the next
    premarket reset.
    """
    phase = _market_phase(now)
    if phase == "premarket":
        return _blank_template(now)

    if date_iso is None:
        if phase == "open" or now.weekday() < 5:
            date_iso = now.date().isoformat()
        else:
            date_iso = _latest_session_date(now) or now.date().isoformat()

    mood = _macro_mood(now)
    rev = _reversion_summary(now)

    if phase == "open":
        context = _context_line(now, "market OPEN · paper practice only")
    else:
        context = _context_line(now, f"session closed · final — {date_iso} (holds until premarket)")

    blocks = [
        TITLE,
        context,
        _one_breath(rev),
        "---",
        _right_now_section(rev, mood),
        "---",
        _practice_corner_section(rev),
        _thinking_section(mood),
        _learned_section(rev),
        _timeline_section(mood, rev, now),
        "---",
        _watcher_list_section(),
        "---",
        _footer(),
    ]
    body = "\n\n".join(b.strip() for b in blocks if b and b.strip())
    return f"{FRONTMATTER}\n\n{body}\n"


def write(now: datetime | None = None) -> None:
    """Render and write the dashboard file. Best-effort; never raises."""
    now = now or datetime.now(tz=ET)
    try:
        target = _vault_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(now), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — must never break a scan
        print(f"[dashboard] write failed: {e}", file=sys.stderr)


def _main() -> None:
    # `python dashboard.py`                  -> write the live file (real now)
    # `python dashboard.py --preview DATE`   -> print a mid-day render of a past
    #                                           day to stdout (no live-file write)
    if len(sys.argv) >= 3 and sys.argv[1] == "--preview":
        date_iso = sys.argv[2]
        y, m, d = (int(x) for x in date_iso.split("-"))
        forced = datetime(y, m, d, 13, 0, tzinfo=ET)
        print(render(forced, date_iso=date_iso))
        return
    write()
    print(f"[dashboard] wrote {_vault_file()}")


if __name__ == "__main__":
    _main()
