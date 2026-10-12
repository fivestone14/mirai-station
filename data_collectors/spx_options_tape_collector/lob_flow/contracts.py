"""
contracts.py — the SHARED LANGUAGE of the box: every data shape, adapter
interface, and knob lives here so the rest of the package (and any outside
project) can import the vocabulary without pulling in any logic.

Vocabulary: GRAVITY = dealer positioning pull (the GEX map handed in from
outside). FLOW = live force (what this package measures: the tape and the
resting-size behavior on the chain).

Nothing in this module does I/O or math. It is the dependency-inversion seam
that keeps the box standalone: mirai (or any host) implements/injects these
Protocols; the sensors and engine only ever see the frozen dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional, Protocol, Sequence

# ---------------------------------------------------------------------------
# Frozen data rows (what the feeds emit, what the sensors consume)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuoteRow:
    """One contract's top-of-book snapshot from a chain sweep ("one light on
    the heatmap"): price + RESTING SIZE on each side, plus the delta used to
    place it in a moneyness bucket."""
    root: str                    # e.g. "SPXW"
    expiry: str                  # ISO date
    right: str                   # "call" | "put"
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[int]
    ask_size: Optional[int]
    delta: Optional[float]       # signed; bucketing uses abs()
    ts: str                      # ISO ET timestamp of the sweep


@dataclass(frozen=True)
class TradeEvent:
    """One option trade WITH the quote that prevailed when it printed —
    the event-time record that lets refill be measured at millisecond
    resolution no matter how slowly we poll."""
    ts_ms: int                   # epoch millis of the print
    strike: float
    right: str                   # "call" | "put"
    price: float
    size: int
    bid: Optional[float]         # NBBO at the print
    ask: Optional[float]
    bid_size: Optional[int]      # resting size at the print
    ask_size: Optional[int]
    condition: Optional[int] = None   # exchange trade condition (complex-leg
                                      # calibration input; carried, not yet keyed)


@dataclass(frozen=True)
class DepthSample:
    """One top-of-book sample of the CONTROL instrument (SPY): the proven,
    boring version of the same scatter/huddle question."""
    ts: str                      # ISO ET
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[int]
    ask_size: Optional[int]


@dataclass(frozen=True)
class BlockInfo:
    """Calendar gate answer: are we inside a scheduled-event window where
    dealer pull-back is NORMAL (and alerts must stay quiet)?"""
    blocked: bool
    kind: Optional[str] = None   # "FOMC" | "CPI" | "NFP" | "OPEX" | None
    until: Optional[str] = None  # ISO ET end of the blocked window


@dataclass(frozen=True)
class LayerView:
    """The generic view a layer contributes to the reasoning stack: a lean,
    a confidence, a stack weight (0.0 while shadow), and a veto flag.
    This is the contract the future weighted-layer learning system reads."""
    layer: str                   # "lob_flow" | "spy_depth"
    ts: str                      # ISO ET
    bias: float                  # [-1, +1] signed lean
    confidence: float            # [0, 1] data sufficiency x baseline maturity
    weight: float                # stack weight; 0.0 in shadow
    veto: bool                   # True on purge/mechanical event or calendar block
    annotations: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter Protocols (the edges of the box — hosts implement these)
# ---------------------------------------------------------------------------


class OptionsQuoteFeed(Protocol):
    """Chain sweeper: full 0DTE top-of-book WITH sizes, one call.
    Returns (spot, rows) — spot may be None when the sweep found nothing."""
    def sweep(self, root: str, expiry: str,
              now: datetime) -> tuple[Optional[float], list[QuoteRow]]: ...


class OptionsTapeFeed(Protocol):
    """Tape puller: incremental trades (with quote-at-print) since a cursor."""
    def trades_since(self, root: str, expiry: str,
                     contracts: Sequence[tuple[float, str]],
                     cursor: dict) -> tuple[list[TradeEvent], dict]: ...


class StreamFeed(Protocol):
    """One push connection: control-instrument depth + option focus set.
    Runs until should_stop() or a transport error (raised to the caller)."""
    async def run(self, equity_symbol: str, option_symbols: Sequence[str],
                  on_depth: Callable[[DepthSample], None],
                  on_option_quote: Optional[Callable[[dict], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None) -> None: ...


class GexContext(Protocol):
    """The GRAVITY handoff: which strikes the map says matter right now
    (magnet / walls / flip). File-mediated in the mirai wiring."""
    def strikes(self, ticker: str) -> Optional[dict]: ...
    # -> {"magnet": float, "call_wall": float, "put_wall": float,
    #     "gamma_flip": float, "sigma": float, "ts": str} | None


class EventCalendar(Protocol):
    """Scheduled-event awareness: FOMC/CPI/NFP days + OPEX."""
    def block(self, now: datetime) -> BlockInfo: ...
    def is_clean_day(self, day: date) -> bool: ...


Clock = Callable[[], datetime]          # injected ET-aware "now" (tests fake it)
VixSource = Callable[[], Optional[float]]   # spread baselines condition on VIX


# ---------------------------------------------------------------------------
# LobConfig — every knob, one place (thresholds come from the research spec)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LobConfig:
    """All tunables. Defaults follow the evidence-ranked spec:
    robust-z 8-10 for size/spread, 20-30 + absolute floor for message rates,
    >=2-bin persistence, ~20s-scale refill half-lives, 21 clean-day baselines."""
    ticker: str = "SPX"
    root: str = "SPXW"                       # 0DTE dailies root
    control_symbol: str = "SPY"
    strike_step: float = 5.0                 # the strike grid everything snaps to

    # moneyness buckets by |delta| — recomputed per sweep, never from stale greeks
    delta_buckets: tuple = (("d00_10", 0.00, 0.10), ("d10_25", 0.10, 0.25),
                            ("d25_40", 0.25, 0.40), ("d40_50", 0.40, 0.50))

    # cadences (seconds)
    quote_sweep_s: int = 60                  # current hosted-MCP freshness ceiling
    tape_poll_s: int = 30
    fold_s: int = 60

    # session blocks (ET minutes from midnight) — open/close judged separately
    open_start_min: int = 9 * 60 + 30
    open_block_end_min: int = 9 * 60 + 45
    close_block_start_min: int = 15 * 60 + 45
    session_end_min: int = 16 * 60

    # robust-z thresholds. Calibrated against BIN-LEVEL dispersion (21 clean
    # days x ~5 bins/slot): the literature's 8-30 sigma bars assume thousands
    # of samples; against daily-median dispersion a size z of -8 was shown to
    # be arithmetically unreachable in most keys (size >= 0 bounds the drop).
    size_z_alert: float = 4.0                # size-at-touch scatter
    spread_z_alert: float = 4.0              # spread blowout
    bucket_min_contracts: int = 4            # a 1-contract "median" drives nothing
    # (noise has NO alert path yet — noise_z is recorded for calibration only;
    #  a real revisions/sec alert waits for full-chain streaming)

    # metric quantization floors for MAD (one lot / one tick / one rev):
    # a frozen 21-day history must not turn a single-tick wiggle into z=200
    metric_quantum: tuple = (("size", 1.0), ("spread", 0.05), ("noise", 0.5))

    # scatter persistence — a single-bin blip is never a signal, and bins
    # separated by a feed gap are not "consecutive"
    scatter_min_bins: int = 2
    bin_adjacency_factor: float = 2.5        # max gap = factor x quote_sweep_s
    fresh_window_factor: float = 3.0         # bins older than this x sweep are dead

    # purge / mass-cancel signature: step-drop across most buckets in ONE bin
    purge_min_buckets: int = 3
    purge_drop_frac: float = 0.6
    purge_suppress_min: int = 10

    # refill defense test at GRAVITY strikes
    refill_recover_frac: float = 0.75
    refill_defended_s: float = 60.0          # median half-life at/below = defended
    refill_min_events: int = 3
    refill_min_bite_frac: float = 0.25       # smaller eats can't measure recovery

    # tape signing
    indeterminate_pos: float = 0.10          # |VWAP position in spread| below = can't tell

    # baselines
    baseline_days: int = 21                  # clean trading days for robust z
    baseline_percentile_days: int = 15       # percentile-band mode from here
    baseline_ewma_halflife_d: float = 10.0
    baseline_samples_per_day: int = 12       # bin-level samples kept per key/day
    mad_floor: float = 1e-9
    band_borrow_mad_inflate: float = 1.5     # cold VIX band borrows its neighbor
                                             # at inflated MAD instead of going mute

    # freshness / storage
    latest_max_age_s: int = 600
    raw_retention_days: int = 30
    disk_cap_mb: int = 500

    # promotion (dormant until proven)
    promote_min_episodes: int = 30
    wilson_z: float = 1.645                  # 90% CI
    confidence_clamp: tuple = (0.85, 1.15)   # the ONLY influence ever allowed

    # package-card grading heuristics (v1, documented as heuristics).
    # The turbulence bar scales with the WINDOW and the day's sigma: expected
    # 30-min range of a diffusion is ~1.6 x sigma x sqrt(30/390); a hit must
    # beat expected by ~50% (mult 2.4 ~= 1.6 x 1.5), else a stopped clock
    # grades near 50%.
    grade_scatter_range_mult: float = 2.4    # x sigma x sqrt(window/390)
    grade_scatter_window_min: int = 30
    grade_pin_sigma: float = 0.3             # held near defended strike at +60 min
    grade_pin_window_min: int = 60

    # late-session cutoff: after this ET minute, defense verdicts are
    # recorded but never assert a regime (charm/pin mechanics make both
    # "defended" and "abandoned" unreliable, and +60min grading self-censors)
    defense_cutoff_min: int = 15 * 60 + 15
