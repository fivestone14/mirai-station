"""The data_sources block: when the read was cut, and how fresh each feed was at that instant.

Every age is measured from the files the station saved, at the cut: the newest finished SPX bar, the diary
row the read stands on (with which options book it carried and whether its views were complete), the newest
market snapshot (and the symbols that failed in it), the newest fold of the 0DTE options tape (its 15-minute
trade count against the prior sessions' usual at that clock, and a content verdict: ok, stale when the fold
is older than the station's own tape age limit, empty when it counted no trades), the newest headline
capture, and the dated options book the read could know. The Schwab login's days left are not on file: the
station's auth-watch reads them out of the encrypted token through the vault, and this package reads files
only, so the field is declared absent rather than guessed. What the loader could not load is logged on the
payload line (``quality_flags.load_notes``) for the reviewer and never put in the scene: a note is free text
from an exception, which may carry a price, a day or a word the leak checks refuse.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta

from ... import station_stores
from ...control import ET
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs, TapeFold
from .news import feed_known_on, newest_capture_before

# The station's auth-watch (runtime/watch/intraday/auth_check.py) reads the login's creation time out of the Schwab
# token, a Fernet-encrypted blob whose key lives in the macOS Keychain (skills/iv-viability/vault.py), and writes no
# status file. This package reads saved files and never a secret, so the days left are declared absent, not computed.
SCHWAB_LOGIN_NOT_READABLE = "not_readable"
COMPLETE_COVERAGE_VIEWS = ("gex_views", "dex_views", "range_ruler")   # a row carrying all three is a complete read of the book
COVERAGE_COMPLETE, COVERAGE_PARTIAL = "complete", "partial"
TAPE_OK, TAPE_STALE, TAPE_EMPTY = "ok", "stale", "empty"


def build_data_sources_block(inputs: FrozenInputs) -> BlockResult:
    """The cut time and every feed's age at it, with what each could not say."""
    result = BlockResult()
    result.leave_out("data_sources.schwab_login_days_left", SCHWAB_LOGIN_NOT_READABLE)
    result.data = units.clean({
        "cut_at": _hhmmss(inputs.cut),
        "spx_bars": _spx_bars(inputs, result),
        "options_diary": _options_diary(inputs, result),
        "market_feed": _market_feed(inputs, result),
        "tape": _tape(inputs, result),
        "headlines": _headlines(inputs, result),
        "dated_book": _dated_book(inputs, result),
    })
    return result


def _spx_bars(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The newest bar finished by the cut: a bar stamped HH:MM finishes at HH:MM+1."""
    bars = inputs.scene.bars
    if not bars:
        result.leave_out("data_sources.spx_bars", "no_finished_bar")
        return None
    stamped = _et(bars[-1]["ts"])
    return {"last_finished": units.hhmm(stamped), "age_s": _age_s(stamped + timedelta(minutes=1), inputs.cut)}


def _options_diary(inputs: FrozenInputs, result: BlockResult) -> dict:
    """The diary row the read stands on: its age (0 for the read's own row), its book and its coverage, read off the
    raw row (the scene's row is the labeller's cut of it) when the loader found it."""
    row = inputs.raw_diary_row or inputs.scene.row
    row_at = _et(row["ts"])
    if inputs.options_book is None:
        result.leave_out("data_sources.options_diary.book", "no_gex_source")
    complete = all(row.get(view) for view in COMPLETE_COVERAGE_VIEWS)
    return units.clean({"row_at": _hhmmss(row_at), "age_s": _age_s(row_at, inputs.cut), "book": inputs.options_book,
                        "coverage": COVERAGE_COMPLETE if complete else COVERAGE_PARTIAL})


def _market_feed(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The newest market snapshot at or before the cut, and the calls that failed in it."""
    snapshot = inputs.market_snapshot
    at = _stamp(snapshot.get("ts")) if snapshot else None
    if at is None:
        result.leave_out("data_sources.market_feed", "no_snapshot_before_cut")
        return None
    failed = [str(entry).partition(":")[0] for entry in (snapshot.get("failed") or [])]
    return {"snapshot_at": _hhmmss(at), "age_s": _age_s(at, inputs.cut), "failed": failed}


def _tape(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The newest fold of the 0DTE tape at or before the cut: its age, its trade count against the usual, and whether
    its content can be read from (ok), is too old by the station's tape age limit (stale), or counted nothing (empty)."""
    max_age = timedelta(minutes=_state_builder().OPTIONS_TAPE_MAX_AGE_MIN)
    fold = _newest_fold(inputs.tape_folds.get(inputs.day) or [], inputs.cut)
    if fold is None:
        result.leave_out("data_sources.tape", "no_fold_before_cut")
        return None
    age = inputs.cut - fold.at
    content = TAPE_EMPTY if not fold.trades else TAPE_STALE if age > max_age else TAPE_OK
    ratio = None
    if fold.trades is None:
        result.leave_out("data_sources.tape.trades_15m_vs_usual_x", "no_trade_count")
    else:
        usual = _usual_trades(inputs, max_age)
        if usual is None:
            result.leave_out("data_sources.tape.trades_15m_vs_usual_x", "too_few_prior_sessions")
        else:
            ratio = round(fold.trades / usual, 2)
    return units.clean({"fold_at": _hhmmss(fold.at), "age_s": _age_s(fold.at, inputs.cut), "trades_15m_vs_usual_x": ratio,
                        "content": content})


def _usual_trades(inputs: FrozenInputs, max_age: timedelta) -> float | None:
    """The median 15-minute trade count at the cut's clock on the prior sessions the scene knows (newest first, up to
    20): each session's newest fold at or before that clock, within the tape age limit, with trades (a fold that
    counted nothing is a dead feed, not a quiet session), needing the station's minimum sessions for a same-clock
    median (cuts.MIN_RANK_SESSIONS)."""
    clock = inputs.cut.timetz()
    counts = []
    for day in inputs.scene.prior_bars:
        then = datetime.combine(date.fromisoformat(day), clock)
        fold = _newest_fold(inputs.tape_folds.get(day) or [], then)
        if fold is not None and fold.trades and then - fold.at <= max_age:
            counts.append(fold.trades)
    return statistics.median(counts) if len(counts) >= _cuts().MIN_RANK_SESSIONS else None


def _newest_fold(folds: list[TapeFold], before: datetime) -> TapeFold | None:
    return max((fold for fold in folds if fold.at <= before), key=lambda fold: fold.at, default=None)


def _headlines(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    if not feed_known_on(inputs.state_dir, inputs.day):
        result.leave_out("data_sources.headlines", "no_headline_feed")
        return None
    newest = newest_capture_before(inputs.state_dir, inputs.cut)
    if newest is None:
        result.leave_out("data_sources.headlines", "no_capture_before_cut")
        return None
    return {"last_capture": _hhmmss(newest), "age_s": _age_s(newest, inputs.cut)}


def _dated_book(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    if inputs.dated_book is None:
        result.leave_out("data_sources.dated_book", "none_known_at_cut")
        return None
    as_of = _stamp(inputs.dated_book.get("as_of"))
    if as_of is None:
        result.leave_out("data_sources.dated_book", "as_of_unreadable")
        return None
    return {"as_of": units.hhmm(as_of), "age_h": round((inputs.cut - as_of).total_seconds() / 3600, 1)}


def _stamp(value: object) -> datetime | None:
    """An ISO timestamp from a saved line as an ET datetime, or None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return _et(value)
    except ValueError:
        return None


def _et(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(ET)


def _hhmmss(when: datetime) -> str:
    return when.strftime("%H:%M:%S")


def _age_s(earlier: datetime, cut: datetime) -> int:
    return int((cut - earlier).total_seconds())


def _state_builder():
    return station_stores.import_spx_jev("state_builder")


def _cuts():
    return station_stores.import_spx_jev("cuts")
