"""
lob_bridge.py — the ONLY coupling point between Mirai and the Layer-2 LOB
(FLOW) sensor box at skills/lob-flow. Everything mirai-specific about the box
lives here: paths, secrets, the Schwab/MCP adapters, and the two file
handshakes the 5-min tick uses (write the gravity strikes down, read the
collector's latest fold up). The package itself never imports mirai code.

Tick-side surface (called from reversion_lens.evaluate, best-effort):
    attach_telemetry(telemetry, ticker, now, magnet=..., ...)   # file-only, no network

Ops surface (called by runner scripts):
    python lob_bridge.py --run-collector      # the launchd daemon entry
    python lob_bridge.py --nightly [day]      # after-close card + baseline fold

Promotion (P5, DORMANT): LOB_LIVE is False and nothing on the trading path
calls apply_confidence(). Flipping LOB_LIVE alone changes nothing until the
earn-trust gate (beat gravity-alone AND the spy control, Wilson 90%, >=30
episodes) also passes — and even then the only permitted influence is a
confidence nudge clamped to [0.85, 1.15].
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

SKILL_DIR = Path(__file__).resolve().parent
_IV = str(SKILL_DIR.parent / "iv-viability")
if _IV not in sys.path:
    sys.path.insert(0, _IV)

ET = ZoneInfo("America/New_York")
STATE_DIR = SKILL_DIR.parent.parent / "state" / "lob_flow"
GEX_CONTEXT_MAX_AGE_S = 1800          # six missed 5-min ticks = the map is stale
LOB_LIVE = False                      # P5 switch — dormant by design


def _lob():
    """Import the box lazily so a missing package degrades to 'sensor absent'
    instead of breaking the scan import chain."""
    import lob_flow
    return lob_flow


def _state():
    from lob_flow.io_state import StateDir
    return StateDir(STATE_DIR)


# ---------------------------------------------------------------------------
# Tick-side handshakes (file-only; NEVER a network call)
# ---------------------------------------------------------------------------


def write_gex_context(ticker: str, *, magnet=None, call_wall=None, put_wall=None,
                      gamma_flip=None, sigma=None, now: Optional[datetime] = None) -> None:
    """Hand the GRAVITY strikes down to the collector (which strikes the
    defense test should watch). Values the scan already has in hand.
    SPX-scoped: a manual scan of any other ticker must not clobber the one
    context slot the collector reads."""
    from lob_flow.io_state import atomic_write_json
    if ticker != _lob().LobConfig().ticker:
        return
    now = now or datetime.now(tz=ET)
    atomic_write_json(_state().gex_context, {
        "ticker": ticker, "magnet": magnet, "call_wall": call_wall,
        "put_wall": put_wall, "gamma_flip": gamma_flip, "sigma": sigma,
        "ts": now.isoformat()})


def read_latest(ticker: str, now: Optional[datetime] = None) -> Optional[dict]:
    """The collector's latest fold — {'lob_flow': {...}, 'spy_depth': {...}}
    or None when stale/absent or recorded for a DIFFERENT ticker (the fold is
    SPX-scoped; it must never attach to another ticker's diary row)."""
    now = now or datetime.now(tz=ET)
    latest = _state().read_latest(now, _lob().LobConfig().latest_max_age_s)
    if not latest:
        return None
    if latest.get("ticker") not in (None, ticker):
        return None
    return {k: latest.get(k) for k in ("lob_flow", "spy_depth") if latest.get(k)}


def attach_telemetry(telemetry: dict, ticker: str, now: datetime, *,
                     magnet=None, call_wall=None, put_wall=None,
                     gamma_flip=None, sigma=None) -> None:
    """The ONE call the scan makes: write the gravity handoff, read the fold,
    attach the engine keys. Any failure leaves telemetry untouched — the
    sensor simply reads as absent for that scan."""
    try:
        write_gex_context(ticker, magnet=magnet, call_wall=call_wall,
                          put_wall=put_wall, gamma_flip=gamma_flip,
                          sigma=sigma, now=now)
        latest = read_latest(ticker, now)
        if latest:
            telemetry.update(latest)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Promotion gate (P5 — dormant; NOT called from any trading path)
# ---------------------------------------------------------------------------


def promotion_gate() -> tuple[bool, str]:
    """Earn-trust check over the nightly polarity ledger. Fail-closed, and —
    like the host's own gate — only REAL-RANGE (ohlc) bar days count: grades
    built on flat point-bars are estimates, not evidence."""
    try:
        lob = _lob()
        hist = SKILL_DIR.parent.parent / "state" / "reversion" / "polarity_history.jsonl"
        rows = []
        for ln in hist.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            kinds = (rec.get("meta") or {}).get("bars_kind") or {}
            if not kinds or all(k != "ohlc" for k in kinds.values()):
                continue
            rows.append(rec)
        return lob.promotion_check(rows, lob.LobConfig())
    except Exception as e:  # noqa: BLE001 — no record, no trust
        return False, f"gate unreadable ({type(e).__name__}) — staying dormant"


def apply_confidence(confidence: float, ticker: str,
                     now: Optional[datetime] = None) -> float:
    """The clamped whisper (UNWIRED until promotion): scale a confidence by
    the box's read. Returns the input unchanged unless LOB_LIVE AND the gate
    passes. Can never touch direction, score, strike, or magnet — it only
    multiplies the number it is given, within [0.85, 1.15]."""
    try:
        if not LOB_LIVE:
            return confidence
        promoted, _ = promotion_gate()
        if not promoted:
            return confidence
        latest = read_latest(ticker, now) or {}
        e = latest.get("lob_flow") or {}
        lob = _lob()
        cfg = lob.LobConfig()
        m = lob.confidence_modifier(
            True, bool(e.get("scatter")),
            e.get("magnet_source") == "defense", cfg)
        lo, hi = cfg.confidence_clamp
        return confidence * min(hi, max(lo, m))
    except Exception:  # noqa: BLE001 — fail closed: unchanged confidence
        return confidence


# ---------------------------------------------------------------------------
# Ops: the collector entry + the nightly pass
# ---------------------------------------------------------------------------


def _build_adapters():
    """All mirai-specific plumbing, injected into the box at its edges."""
    import iv_fetcher
    import native_gex_feed
    import vault
    from lob_flow.baselines import FileCalendar
    from lob_flow.contracts import LobConfig
    from lob_flow.daemon import Adapters
    from lob_flow.options_feed import (McpClient, OptionsQuoteFeedMcp,
                                       OptionsTapeFeedMcp)

    cfg = LobConfig()
    client = McpClient(native_gex_feed.ENDPOINT, vault.get_cassandra_token,
                       harden=vault.install_runtime_hardening)   # OWN session
    stream = None
    try:
        from lob_flow.spy_stream import SchwabStream
        stream = SchwabStream(iv_fetcher._build_client)
    except Exception:  # noqa: BLE001 — no schwab stream -> poll-only tier
        stream = None

    def _vix():
        try:
            import lefteye_fetcher
            return lefteye_fetcher.live_spot("$VIX")
        except Exception:  # noqa: BLE001
            return None

    class FileGexContext:
        def strikes(self, ticker: str):
            obj = _read_json(_state().gex_context)
            if not obj or obj.get("ticker") != ticker:
                return None
            try:
                age = (datetime.now(tz=ET)
                       - datetime.fromisoformat(obj["ts"])).total_seconds()
            except (KeyError, ValueError, TypeError):
                return None
            return obj if age <= GEX_CONTEXT_MAX_AGE_S else None

    def _is_live() -> bool:
        try:
            _rt = str(SKILL_DIR.parent.parent / "runtime")
            if _rt not in sys.path:
                sys.path.insert(0, _rt)
            from watch.intraday import market_status
            return bool(market_status.check().is_live)
        except Exception:  # noqa: BLE001 — unknown = keep running to the clock
            return True

    return cfg, Adapters(
        quote_feed=OptionsQuoteFeedMcp(client),
        tape_feed=OptionsTapeFeedMcp(client),
        schwab_stream=stream,
        gex=FileGexContext(),
        calendar=FileCalendar(_state().calendar),
        clock=lambda: datetime.now(tz=ET),
        vix=_vix,
        state=_state(),
        is_live=_is_live)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def run_collector() -> int:
    from lob_flow import daemon
    cfg, adapters = _build_adapters()
    return daemon.main(cfg, adapters)


def nightly(day: Optional[str] = None) -> None:
    """After the host grader: (1) the package's own head-to-head card,
    (2) fold the finished day into both baseline stores. Best-effort."""
    import gex_polarity_ab
    from lob_flow import LobConfig, grade_day
    from lob_flow.baselines import FileCalendar, fold_day_into_baselines
    from lob_flow.io_state import atomic_write_json

    day = day or datetime.now(tz=ET).date().isoformat()
    cfg = LobConfig()
    sd = _state()
    diary = SKILL_DIR.parent.parent / "state" / "reversion" / f"{day}.jsonl"
    rows = []
    if diary.exists():
        for ln in diary.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue                       # torn line, not a dead night
    bars = gex_polarity_ab._default_bars("SPX", day)
    card = grade_day(day, rows, bars, cfg)
    atomic_write_json(sd.card(day), card)
    cal = FileCalendar(sd.calendar)
    for eng in ("lob_flow", "spy_depth"):
        rep = fold_day_into_baselines(sd, day, cfg, cal, eng)
        print(f"lob nightly: {eng} fold clean={rep['clean']} folded={rep['folded']} "
              f"clean_days={rep['clean_days']}")
    for eng, c in card["engines"].items():
        print(f"lob nightly: {eng} card scatter {c['scatter']['hits']}/"
              f"{c['scatter']['n']} defense {c['defense']['hits']}/{c['defense']['n']}")
    gate, reason = promotion_gate()
    print(f"lob nightly: promotion gate -> {gate} ({reason})")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--run-collector" in args:
        raise SystemExit(run_collector())
    if "--nightly" in args:
        day = next((a for a in args if not a.startswith("-")), None)
        nightly(day)
    elif "--status" in args:
        latest = read_latest("SPX")
        gate, reason = promotion_gate()
        print("latest fold:", "fresh" if latest else "absent/stale")
        print("promotion:", gate, "-", reason)
        print("LOB_LIVE:", LOB_LIVE)
    else:
        print("usage: lob_bridge.py [--run-collector | --nightly [day] | --status]")
