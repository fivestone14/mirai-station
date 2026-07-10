"""
daemon.py — the COLLECTOR: the only long-lived process and the only place in
the box that touches a network. Four cooperating loops on one asyncio event
loop, all state through io_state, all math delegated to sensors/engine:

  T1  quote sweep   (60s)  chain top-of-book WITH sizes -> sweep bins
  T2  tape poll     (30s)  incremental trades (quote-at-print) -> tape window
  T3  schwab stream (push) SPY depth samples + SPXW focus-set revisions
  T4  fold          (60s)  evaluate_once -> latest.json + agg journal + health

Fail-closed posture: every window handed to the fold is age-gated (a feed
that died keeps its stale picture OUT of the fold), tape events hit the
journal BEFORE the cursor advances (a crash can re-pull, never lose), gaps
are marked and never interpolated, and a dead loop is reported in health.

Lifecycle: launchd StartInterval=60 respawns the runner; the runner gates on
market hours and the flock, so a crash self-heals within a minute and two
collectors can never run at once. The daemon self-terminates after the close
(or when the host's is_live hook says the session ended early).
"""
from __future__ import annotations

import asyncio
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from . import engine as _engine
from .baselines import BaselineStore, FileCalendar
from .contracts import DepthSample, LobConfig, TradeEvent
from .io_state import StateDir, load_cursor, save_cursor
from .spy_stream import spxw_symbol

TAPE_WINDOW_MIN = 15          # rolling trade window the sensors read
TAPE_SEED_MIN = 5             # a NEW focus contract starts its tape this far
                              # back — enough for refill tests, small enough
                              # that the catch-up pull can't blow the timeout
SWEEP_BIN_KEEP = 10           # recent sweep bins kept for scoring
SPY_WINDOW_MIN = 5            # control samples older than this are dead
VIX_REFRESH_S = 300
FOCUS_ATM_STRIKES = 10        # ATM +/- N strikes (per right) in the focus set
FOCUS_DRIFT_RESUB = 10        # re-subscribe when the focus set drifts this far
FOCUS_CHECK_S = 60
SESSION_GRACE_MIN = 5         # keep folding a little past the close
CLOSE_POLL_S = 20
STREAM_STABLE_S = 60          # a connection older than this resets the backoff
MAX_STREAM_SYMBOLS = 60


@dataclass
class Adapters:
    """Everything injected from the host — the box's only view of the world."""
    quote_feed: object                 # OptionsQuoteFeed
    tape_feed: object                  # OptionsTapeFeed
    schwab_stream: Optional[object]    # StreamFeed | None
    gex: object                        # GexContext
    calendar: FileCalendar
    clock: Callable[[], datetime]      # ET-aware now()
    vix: Callable[[], Optional[float]]
    state: StateDir
    is_live: Optional[Callable[[], bool]] = None   # host market-status hook


@dataclass
class _Mem:
    """The in-memory working set T4 folds from."""
    sweep_bins: deque = field(default_factory=lambda: deque(maxlen=SWEEP_BIN_KEEP))
    fingerprint: Optional[dict] = None
    deltas: dict = field(default_factory=dict)            # strike|right -> delta
    spot: Optional[float] = None
    trades: deque = field(default_factory=deque)          # TradeEvents, time-pruned
    seen_tape: set = field(default_factory=set)           # replay/crash dedupe keys
    spy_samples: deque = field(default_factory=deque)     # DepthSamples, time-pruned
    option_revisions: int = 0                             # streamed focus-set count
    last_rev_drain: Optional[datetime] = None
    vix: Optional[float] = None
    vix_at: Optional[datetime] = None
    stream_up: bool = False
    purge_until: Optional[datetime] = None                # sticky mass-cancel veto
    fed_until: Optional[str] = None                       # newest bin already folded
    dead_loops: set = field(default_factory=set)


def main(cfg: LobConfig, adapters: Adapters) -> int:
    """Entry point (the host runner calls this). Returns an exit code."""
    now = adapters.clock()
    sd = adapters.state
    lock = sd.acquire_lock()
    if lock is None:
        return 0                                       # another collector owns today
    sd.rotate(now, cfg.raw_retention_days, cfg.disk_cap_mb)
    mem = _Mem()
    _replay_today(sd, mem, now)
    try:
        asyncio.run(_run_all(cfg, adapters, mem))
    finally:
        lock.close()
    return 0


def _day(now: datetime) -> str:
    return now.date().isoformat()


def _past_close(now: datetime, cfg: LobConfig) -> bool:
    return (now.hour * 60 + now.minute) >= cfg.session_end_min + SESSION_GRACE_MIN


def _tape_key(e) -> tuple:
    """Identity of one print for crash-replay dedupe (dict or TradeEvent)."""
    g = e.get if isinstance(e, dict) else lambda k, d=None: getattr(e, k, d)
    return (g("ts_ms"), g("strike"), g("right"), g("price"), g("size"))


def _replay_today(sd: StateDir, mem: _Mem, now: datetime) -> None:
    """Re-seed the working windows from today's journals after a restart —
    the dial needs consecutive bins and the defense test needs its trade
    window; a 60s-supervised crash must not blind either for 15 minutes.
    Every journaled print seeds the dedupe set: a crash between journal-append
    and cursor-save re-pulls those prints, and they must not double-count."""
    rows = list(sd.rows(sd.raw(_day(now), "sweeps")))
    for r in rows[-SWEEP_BIN_KEEP:]:
        if not r.get("gap"):
            mem.sweep_bins.append(r)
    cutoff = (now - timedelta(minutes=TAPE_WINDOW_MIN)).timestamp() * 1000
    for r in sd.rows(sd.raw(_day(now), "tape")):
        if r.get("gap"):
            continue
        mem.seen_tape.add(_tape_key(r))
        if (r.get("ts_ms") or 0) < cutoff:
            continue
        try:
            mem.trades.append(TradeEvent(**{k: r.get(k) for k in (
                "ts_ms", "strike", "right", "price", "size",
                "bid", "ask", "bid_size", "ask_size", "condition")}))
        except (KeyError, TypeError):
            continue
    latest = sd.read_latest(now, max_age_s=24 * 3600) or {}
    pu = latest.get("purge_until")                # the veto survives a restart
    if pu:
        try:
            t = datetime.fromisoformat(pu)
            if t > now:
                mem.purge_until = t
        except (ValueError, TypeError):
            pass


async def _run_all(cfg: LobConfig, adapters: Adapters, mem: _Mem) -> None:
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(_guard("sweep", _sweep_loop(cfg, adapters, mem, stop), mem)),
        asyncio.create_task(_guard("tape", _tape_loop(cfg, adapters, mem, stop), mem)),
        asyncio.create_task(_guard("fold", _fold_loop(cfg, adapters, mem, stop), mem)),
    ]
    if adapters.schwab_stream is not None:
        tasks.append(asyncio.create_task(
            _guard("stream", _stream_loop(cfg, adapters, mem, stop), mem)))
    tasks.append(asyncio.create_task(_close_watch(cfg, adapters, stop)))
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _guard(name: str, coro, mem: _Mem) -> None:
    """A loop that dies must be VISIBLE: recorded in mem (the fold publishes
    it in health.json) and printed to the launchd stderr log."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        mem.dead_loops.add(name)
        traceback.print_exc()


async def _close_watch(cfg: LobConfig, adapters: Adapters,
                       stop: asyncio.Event) -> None:
    """Self-terminate past the normal close, or the moment the host's market
    status says the session ended (early-close half days)."""
    while not stop.is_set():
        now = adapters.clock()
        if _past_close(now, cfg):
            stop.set()
            return
        if adapters.is_live is not None:
            try:
                if not adapters.is_live():
                    stop.set()
                    return
            except Exception:  # noqa: BLE001 — a broken hook must not kill the day
                pass
        await asyncio.sleep(CLOSE_POLL_S)


# ---------------------------------------------------------------------------
# T1 — quote sweep
# ---------------------------------------------------------------------------


async def _sweep_loop(cfg: LobConfig, a: Adapters, mem: _Mem,
                      stop: asyncio.Event) -> None:
    from .sensors import aggregate_sweep
    while not stop.is_set():
        now = a.clock()
        try:
            spot, quotes = await asyncio.to_thread(
                a.quote_feed.sweep, cfg.root, _day(now), now)
            if quotes:
                had_prev = mem.fingerprint is not None   # BEFORE the overwrite
                agg = aggregate_sweep(quotes, mem.fingerprint, cfg)
                mem.fingerprint = agg["fingerprint"]
                mem.deltas = agg["deltas"] or mem.deltas
                mem.spot = spot or mem.spot
                bin_row = {"ts": now.isoformat(), "buckets": agg["buckets"],
                           "purge_hint": agg["purge_hint"] if had_prev else None,
                           "revisions_per_s": _rev_per_s(agg["revisions"],
                                                         mem, now, cfg,
                                                         had_prev)}
                mem.sweep_bins.append(bin_row)
                a.state.append(a.state.raw(_day(now), "sweeps"), bin_row)
            else:
                a.state.gap_marker(_day(now), "sweeps", now, "empty sweep")
        except Exception as e:  # noqa: BLE001 — a bad sweep is a gap, not a crash
            a.state.gap_marker(_day(now), "sweeps", now, f"{type(e).__name__}: {e}")
        await asyncio.sleep(cfg.quote_sweep_s)


def _rev_per_s(sweep_revisions: int, mem: _Mem, now: datetime,
               cfg: LobConfig, had_prev: bool) -> Optional[float]:
    """Streamed focus-set revisions win (true count); the sweep diff is the
    chain-wide PROXY. Both divided by the REAL elapsed window, not a nominal
    60s (sweep latency stretches the period). The first sweep after a start
    has no previous fingerprint — that is 'unknowable', never a fake 0.0."""
    elapsed = (now - mem.last_rev_drain).total_seconds() if mem.last_rev_drain \
        else float(cfg.quote_sweep_s)
    mem.last_rev_drain = now
    elapsed = max(1.0, elapsed)
    if mem.stream_up and mem.option_revisions:
        n, mem.option_revisions = mem.option_revisions, 0
        return round(n / elapsed, 3)
    if not had_prev:
        return None
    return round(sweep_revisions / elapsed, 3)


# ---------------------------------------------------------------------------
# T2 — tape poll
# ---------------------------------------------------------------------------


async def _tape_loop(cfg: LobConfig, a: Adapters, mem: _Mem,
                     stop: asyncio.Event) -> None:
    while not stop.is_set():
        now = a.clock()
        try:
            focus = _focus_contracts(cfg, a, mem)
            if focus:
                cursor = load_cursor(a.state, _day(now))
                # a contract entering the focus set starts its tape a few
                # minutes back — a no-cursor full-day pull across ~46
                # contracts is huge, and even 15 min of 0DTE ATM tape is heavy
                edge = int((now - timedelta(minutes=TAPE_SEED_MIN)
                            ).timestamp() * 1000)
                for strike, right in focus:
                    cursor["contracts"].setdefault(f"{strike}|{right}",
                                                   {"ms": edge, "n": 0})
                events, cursor = await asyncio.to_thread(
                    a.tape_feed.trades_since, cfg.root, _day(now), focus, cursor)
                # journal FIRST, cursor after: a crash between the two re-pulls,
                # and the seen-set (seeded from the journal at replay) makes the
                # re-pull a true dedupe instead of a double count
                for e in events:
                    k = _tape_key(e)
                    if k in mem.seen_tape:
                        continue
                    mem.seen_tape.add(k)
                    mem.trades.append(e)
                    a.state.append(a.state.raw(_day(now), "tape"), e.__dict__)
                save_cursor(a.state, cursor)
                _prune_trades(mem, now)
        except Exception as e:  # noqa: BLE001
            a.state.gap_marker(_day(now), "tape", now, f"{type(e).__name__}: {e}")
        await asyncio.sleep(cfg.tape_poll_s)


def _focus_contracts(cfg: LobConfig, a: Adapters,
                     mem: _Mem) -> list[tuple[float, str]]:
    """ATM window (both rights) UNION the gravity strikes — the strikes worth
    paying tape attention to. Re-derived every poll (walls move, spot drifts)."""
    out: list[tuple[float, str]] = []
    step = cfg.strike_step
    if mem.spot:
        atm = round(mem.spot / step) * step
        for i in range(-FOCUS_ATM_STRIKES, FOCUS_ATM_STRIKES + 1):
            for right in ("call", "put"):
                out.append((atm + i * step, right))
    gex = a.gex.strikes(cfg.ticker) or {}
    for k in ("magnet", "call_wall", "put_wall"):
        v = gex.get(k)
        if v is not None:
            s = round(float(v) / step) * step
            for right in ("call", "put"):
                if (s, right) not in out:
                    out.append((s, right))
    return out


def _prune_trades(mem: _Mem, now: datetime) -> None:
    cutoff = (now - timedelta(minutes=TAPE_WINDOW_MIN)).timestamp() * 1000
    while mem.trades and mem.trades[0].ts_ms < cutoff:
        mem.trades.popleft()


# ---------------------------------------------------------------------------
# T3 — schwab stream (SPY depth + SPXW focus set)
# ---------------------------------------------------------------------------


async def _stream_loop(cfg: LobConfig, a: Adapters, mem: _Mem,
                       stop: asyncio.Event) -> None:
    """Connects immediately — the SPY control needs no spot; the option
    focus set starts as whatever gravity gives and the drift check
    re-subscribes once the first sweep sets spot (an MCP outage must never
    silence the independent control)."""
    backoff = 5
    while not stop.is_set():
        now = a.clock()
        connected_at = now
        subscribed = _focus_symbols(cfg, a, mem, now)
        drift = {"checked_at": now}

        def _should_stop() -> bool:
            if stop.is_set():
                return True
            t = a.clock()
            if (t - drift["checked_at"]).total_seconds() >= FOCUS_CHECK_S:
                drift["checked_at"] = t
                fresh = _focus_symbols(cfg, a, mem, t)
                if len(set(fresh) ^ set(subscribed)) > FOCUS_DRIFT_RESUB:
                    return True                    # clean reconnect on new set
            return False

        try:
            mem.stream_up = True
            await a.schwab_stream.run(
                cfg.control_symbol, subscribed,
                on_depth=lambda d: _on_depth(mem, d),
                on_option_quote=lambda q: _on_option_quote(mem, q),
                should_stop=_should_stop)
            backoff = 5                            # clean exit (drift/stop)
        except Exception as e:  # noqa: BLE001 — reconnect with backoff, mark the gap
            a.state.gap_marker(_day(now), "spy-depth", now,
                               f"{type(e).__name__}: {e}")
            if (a.clock() - connected_at).total_seconds() >= STREAM_STABLE_S:
                backoff = 5                        # it held for a while: not a storm
            await asyncio.sleep(min(backoff, 120))
            backoff *= 2
        finally:
            mem.stream_up = False


def _focus_symbols(cfg: LobConfig, a: Adapters, mem: _Mem,
                   now: datetime) -> list[str]:
    return [spxw_symbol(_day(now), right, strike)
            for strike, right in _focus_contracts(cfg, a, mem)][:MAX_STREAM_SYMBOLS]


def _on_depth(mem: _Mem, d: DepthSample) -> None:
    mem.spy_samples.append(d)
    if len(mem.spy_samples) > 10_000:              # hard bound; time-pruned at fold
        mem.spy_samples.popleft()


def _on_option_quote(mem: _Mem, q: dict) -> None:
    mem.option_revisions += 1


# ---------------------------------------------------------------------------
# T4 — fold
# ---------------------------------------------------------------------------


async def _fold_loop(cfg: LobConfig, a: Adapters, mem: _Mem,
                     stop: asyncio.Event) -> None:
    lob_store = BaselineStore(a.state.baseline("lob_flow"), cfg)
    spy_store = BaselineStore(a.state.baseline("spy_depth"), cfg)
    while not stop.is_set():
        await asyncio.sleep(cfg.fold_s)
        try:
            fold_once(cfg, a, mem, lob_store, spy_store)
        except Exception:  # noqa: BLE001 — the fold must survive anything
            traceback.print_exc()


def fold_once(cfg: LobConfig, a: Adapters, mem: _Mem,
              lob_store: BaselineStore, spy_store: BaselineStore) -> dict:
    """One T4 fold, synchronous and testable: sensors -> latest.json + agg.
    Every window is age-pruned HERE (not only when feeds happen to deliver):
    a dead feed's leftovers must never be folded as a live picture."""
    now = a.clock()
    _prune_trades(mem, now)
    if mem.vix_at is None or (now - mem.vix_at).total_seconds() > VIX_REFRESH_S:
        try:
            mem.vix = a.vix()
        except Exception:  # noqa: BLE001 — VIX is a conditioner, not a need
            mem.vix = None
        mem.vix_at = now
    result = _engine.evaluate_once(
        now, cfg,
        lob_bins=list(mem.sweep_bins),         # engine age-gates these too
        tape_trades=list(mem.trades),
        gex_strikes=a.gex.strikes(cfg.ticker),
        spy_bins=_fold_spy_bin(mem, now),
        lob_store=lob_store, spy_store=spy_store,
        cal_block=a.calendar.block(now), vix=mem.vix,
        purge_until=mem.purge_until, fed_until=mem.fed_until,
        deltas=mem.deltas)
    mem.purge_until = result.get("purge_until")
    mem.fed_until = result.get("fed_until")
    views = [v.__dict__ for v in result["layer_views"]]
    a.state.write_latest({"ticker": cfg.ticker,
                          "lob_flow": result["lob_flow"],
                          "spy_depth": result["spy_depth"],
                          "layer_views": views,
                          # the sticky veto must survive a crash-restart
                          "purge_until": (mem.purge_until.isoformat()
                                          if mem.purge_until else None),
                          "tier": _tier(mem)}, now)
    for eng in ("lob_flow", "spy_depth"):
        a.state.append(a.state.agg(_day(now)), {
            "ts": now.isoformat(), "engine": eng,
            "snapshot": result[eng],
            "baseline_rows": result["baseline_rows"][eng]})
    a.state.write_health(now, stream_up=mem.stream_up,
                         sweep_bins=len(mem.sweep_bins),
                         trades_window=len(mem.trades),
                         spy_samples=len(mem.spy_samples),
                         spot=mem.spot, tier=_tier(mem),
                         dead_loops=sorted(mem.dead_loops))
    return result


def _fold_spy_bin(mem: _Mem, now: datetime) -> list[dict]:
    """The last few minutes of SPY samples -> one control bin (median size,
    median spread). Prunes the window HERE — a frozen stream must yield []
    (no opinion), never a restamped stale median."""
    cutoff = (now - timedelta(minutes=SPY_WINDOW_MIN)).isoformat()
    while mem.spy_samples and mem.spy_samples[0].ts < cutoff:
        mem.spy_samples.popleft()
    if not mem.spy_samples:
        return []
    sizes = [s.bid_size + s.ask_size for s in mem.spy_samples
             if s.bid_size is not None and s.ask_size is not None]
    spreads = [s.ask - s.bid for s in mem.spy_samples
               if s.ask is not None and s.bid is not None and s.ask >= s.bid]

    def med(v):
        return sorted(v)[len(v) // 2] if v else None
    return [{"ts": now.isoformat(), "size": med(sizes), "spread": med(spreads)}]


def _tier(mem: _Mem) -> str:
    return "stream+mcp" if mem.stream_up else "mcp_60s_event"
