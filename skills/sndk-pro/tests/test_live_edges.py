"""The edges that touch the outside world — the market clock, the Schwab quote
and minute tape, and the `claude -p` call — driven through fakes at the
broker client and the subprocess, never the real ones.

These are where the recorded outages happened (a broker login that lapsed
mid-morning and stopped the bar record; a scanner that wrote nothing for four
sessions), and until this file no test ran the code between the fakes and the
logic: the gate's import of the market clock, `_quote`, `fetch_session`, the
real command line of the model call, and `sndk_board._main`. Every day is found
by walking the market's own calendar from today, so nothing here pins a date."""
import json
import os
import runpy
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

import lefteye_fetcher
import sndk_bars as SB
import sndk_board as B
import sndk_feed
import sndk_hunter
import sndk_read as SR
import synth
from synth import _REAL_CALL_THE_MODEL, _REAL_CALL_THE_MODEL_V2, _diary_row_with_board, mkrows

ET = ZoneInfo("America/New_York")
STALE_CHAIN_SPOT = round(synth.SPOT * 0.85, 2)     # the chain's own spot runs ~15% stale
QUOTE = {"lastPrice": synth.SPOT + 11.4, "mark": synth.SPOT + 11.3,
         "openPrice": synth.SPOT - 5.0, "highPrice": synth.SPOT + 20.0,
         "lowPrice": synth.SPOT - 12.0, "closePrice": synth.SPOT - 10.0}
OUTSIDE_WORLD_TOOLS = {"Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"}


# --- the market clock -----------------------------------------------------------
def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=ET)


def _closed_moments(day: date) -> dict:
    return {"before_open": _at(day, SB.SESSION_OPEN) - timedelta(minutes=1),
            "at_close": _at(day, SB.SESSION_CLOSE),
            "weekend": _at(day + timedelta(days=5 - day.weekday()), time(11, 0))}


def _gate_clock(monkeypatch, gate):
    """The market-status module exactly as `gate` imports it, from `gate`'s own
    path: a copy left over from an earlier import cannot stand in for it, so a
    gate that can no longer find the clock fails here instead of passing."""
    for name in [m for m in sys.modules if m == "watch" or m.startswith("watch.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if os.path.basename(p) != "runtime"])
    gate._market_live()
    return sys.modules["watch.intraday.market_status"]


class _Market:
    """The market's own calendar, and the wall clock its gate reads."""

    def __init__(self, monkeypatch, status):
        self._monkeypatch, self.status = monkeypatch, status

    def at(self, when: datetime) -> datetime:
        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz) if tz else when.replace(tzinfo=None)
        self._monkeypatch.setattr(self.status, "datetime", _Frozen)
        return when

    def full_session(self, day: date) -> bool:
        return self.status.check(_at(day, SB.SESSION_CLOSE) - timedelta(minutes=1)).is_live

    def session_day(self) -> date:
        """The next Monday-to-Thursday the calendar calls a full session (so
        the front weekly is at least a day out)."""
        d = datetime.now(ET).date()
        while d.weekday() > 3 or not self.full_session(d):
            d += timedelta(days=1)
        return d


@pytest.fixture
def market(monkeypatch):
    return _Market(monkeypatch, _gate_clock(monkeypatch, sndk_hunter))


# --- the broker -----------------------------------------------------------------
class _BrokerDown(Exception):
    pass


class _Response:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _BrokerDown(f"HTTP {self.status_code}")


class _Broker:
    """The authenticated Schwab client, faked: one quote, one minute tape that
    ignores the extended-hours flag, and a way to fail. `fail` is None, "http"
    (a 401), "raise" (the call throws, as a lapsed refresh token does) or
    "no_client" (the client cannot be built at all)."""

    def __init__(self):
        self.quote = {}
        self.tape = []
        self.fail = None
        self.fail_minutes = None
        self.calls = []

    def get_quote(self, symbol):
        self.calls.append("quote")
        return self._answer(self.fail, {symbol: {"quote": self.quote}})

    def get_price_history_every_minute(self, symbol, start_datetime, end_datetime,
                                       need_extended_hours_data):
        self.calls.append("minutes")
        day = start_datetime.astimezone(ET).date()
        return self._answer(self.fail or self.fail_minutes,
                            {"candles": [c for c in self.tape if _candle_at(c).date() == day]})

    @staticmethod
    def _answer(fail, body):
        if fail == "raise":
            raise _BrokerDown("refresh token expired")
        return _Response(401 if fail == "http" else 200, body)


@pytest.fixture
def broker(monkeypatch):
    b = _Broker()

    def client():
        if b.fail == "no_client":
            raise _BrokerDown("token vault unreadable")
        return b
    monkeypatch.setattr(lefteye_fetcher, "_client", client)
    return b


def _candle_at(c: dict) -> datetime:
    return datetime.fromtimestamp(c["datetime"] / 1000, tz=ET)


def _tape(start: datetime, through: datetime) -> list:
    """One candle per minute from `start` to the minute `through` sits in —
    that last one still running, as the broker hands it back."""
    out, t, i = [], start, 0
    while t <= through:
        px = synth.SPOT + (i % 7)
        out.append({"open": px, "high": px + 1.0, "low": px - 1.0, "close": px,
                    "volume": 100 + i, "datetime": int(t.timestamp() * 1000)})
        t, i = t + timedelta(minutes=1), i + 1
    return out


# --- the scanner ----------------------------------------------------------------
def _book_for(now: datetime) -> dict:
    front = now.date() + timedelta(days=(4 - now.weekday()) % 7)
    dte = (front - now.date()).days
    second = front + timedelta(days=7)
    return {"spot": STALE_CHAIN_SPOT,
            "contracts": synth.prepared_book(front_dte=dte, front_expiry=front.isoformat(),
                                             second_dte=dte + 7, second_expiry=second.isoformat()),
            "meta": {"expiries": [{"date": front.isoformat(), "dte": dte},
                                  {"date": second.isoformat(), "dte": dte + 7}],
                     "coverage": {"complete": True, "missing": None,
                                  "front_expiry": front.isoformat(), "front_dead": False,
                                  "expiry_today": dte == 0}}}


@pytest.fixture
def chain(monkeypatch):
    """The chain pull, stubbed; the list records the live spot each pull was anchored on."""
    asked = []

    def sndk_chain(now, live_spot=None):
        asked.append(live_spot)
        return _book_for(now)
    monkeypatch.setattr(sndk_feed, "sndk_chain", sndk_chain)
    return asked


def _diary(now: datetime) -> list:
    p = sndk_hunter._diary_dir() / f"{now.date().isoformat()}.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


@pytest.mark.parametrize("gate", [sndk_hunter, SR], ids=["scanner", "reader"])
def test_the_market_gate_is_open_only_inside_a_regular_session(monkeypatch, gate):
    """The scanner and the reader each find the market's own clock and follow
    it — open from the session open until the close, shut otherwise — and stay
    shut when that clock cannot be read."""
    market = _Market(monkeypatch, _gate_clock(monkeypatch, gate))
    day = market.session_day()
    expected = [(_at(day, SB.SESSION_OPEN), True),
                (_at(day, SB.SESSION_CLOSE) - timedelta(minutes=1), True),
                *((when, False) for when in _closed_moments(day).values())]
    for when, live in expected:
        market.at(when)
        assert gate._market_live() is live, when

    def unreadable(now=None):
        raise RuntimeError("market calendar unavailable")
    market.at(_at(day, time(11, 0)))
    monkeypatch.setattr(market.status, "check", unreadable)
    assert gate._market_live() is False


@pytest.mark.parametrize("quote", [QUOTE, {**QUOTE, "lastPrice": None}], ids=["last", "mark"])
def test_each_live_tick_appends_one_row_anchored_on_the_live_quote(broker, chain, market, quote):
    """Inside a session every tick appends exactly one row for today, its spot
    the live Schwab quote and never the stale chain spot. A later tick reads
    today's earlier rows as the day's memory: after the quote moves, its sigma
    anchor is still the first row's."""
    now = market.at(_at(market.session_day(), time(11, 0)))
    broker.quote = quote
    broker.tape = _tape(_at(now.date(), SB.SESSION_OPEN), now)
    live = quote["lastPrice"] or quote["mark"]

    assert sndk_hunter.tick(now) == 0
    rows = _diary(now)
    assert len(rows) == 1
    assert rows[0]["spot"] == live != STALE_CHAIN_SPOT
    assert rows[0]["prior_close"] == quote["closePrice"]
    assert rows[0]["meta"]["forced"] is False
    assert rows[0]["meta"]["spot_source"] == "schwab_quote"
    assert chain == [live]

    broker.quote = {k: v + 25.0 if k in ("lastPrice", "mark") and v is not None else v
                    for k, v in quote.items()}
    assert sndk_hunter.tick(now + timedelta(minutes=2)) == 0
    rows = _diary(now)
    assert len(rows) == 2 and rows[1]["spot"] == live + 25.0
    assert rows[1]["sigma_anchor"] == rows[0]["sigma_anchor"] != rows[1]["sigma_live"]
    assert chain == [live, live + 25.0]


@pytest.mark.parametrize("failure", ["http", "raise", "no_client", "only_prior_close"])
def test_no_live_quote_means_no_book_and_no_row(broker, chain, market, failure):
    """No live quote → no book, no row: the tick neither crashes nor pulls a
    chain, and the skip is logged. Yesterday's close is never a live spot."""
    now = market.at(_at(market.session_day(), time(11, 0)))
    if failure == "only_prior_close":
        broker.quote = {"lastPrice": None, "mark": None, "closePrice": QUOTE["closePrice"]}
    else:
        broker.quote, broker.fail = dict(QUOTE), failure

    assert sndk_hunter.tick(now) == 0
    assert _diary(now) == []
    assert chain == []
    log = sndk_feed._state_dir() / "fetch_log.jsonl"
    assert any(json.loads(ln).get("skip") for ln in log.read_text().splitlines())


def test_a_failed_minute_bar_fetch_never_costs_the_diary_row(broker, chain, market):
    """The tick's own minute-bar fetch is optional: when it fails the row still
    lands on the live quote, with the bar-derived range simply absent."""
    now = market.at(_at(market.session_day(), time(11, 0)))
    broker.quote, broker.fail_minutes = dict(QUOTE), "raise"

    assert sndk_hunter.tick(now) == 0
    assert "minutes" in broker.calls
    rows = _diary(now)
    assert len(rows) == 1 and rows[0]["spot"] == QUOTE["lastPrice"]
    assert rows[0]["adaptive_em"] is None


@pytest.mark.parametrize("moment", ["before_open", "at_close", "weekend"])
def test_outside_a_session_the_scanner_does_no_work_unless_forced(broker, chain, market, moment):
    """Outside the regular session a tick asks the broker nothing and writes
    nothing; a forced tick still writes one row, marked forced."""
    now = market.at(_closed_moments(market.session_day())[moment])
    broker.quote = dict(QUOTE)

    assert sndk_hunter.tick(now) == 0
    assert broker.calls == [] and chain == [] and _diary(now) == []

    assert sndk_hunter.tick(now, force=True) == 0
    rows = _diary(now)
    assert len(rows) == 1 and rows[0]["meta"]["forced"] is True


@pytest.mark.parametrize("argv, past_the_gate", [([], False), (["--once"], False), (["--force"], True)],
                         ids=["bare", "once", "force"])
def test_only_force_takes_a_command_line_run_past_the_market_gate(broker, market, monkeypatch,
                                                                  argv, past_the_gate):
    """--force is the documented way past the gate, for manual proof runs. Every
    run is already a single tick, so --once asks for nothing more: outside a
    session it asks the broker nothing and pulls no book, like a bare run."""
    market.at(_closed_moments(market.session_day())["before_open"])
    broker.quote = dict(QUOTE)
    pulled = []
    # the book comes back empty, so a run past the gate stops before it builds
    # a row off the wall clock the command line cannot be handed
    monkeypatch.setattr(sndk_feed, "sndk_chain", lambda now, live_spot=None: pulled.append(live_spot))
    monkeypatch.setattr(sys, "argv", [sndk_hunter.__file__, *argv])

    with pytest.raises(SystemExit) as run:
        runpy.run_path(sndk_hunter.__file__, run_name="__main__")

    assert run.value.code == 0
    assert pulled == ([QUOTE["lastPrice"]] if past_the_gate else [])
    assert (broker.calls == []) is not past_the_gate


# --- the minute-bar sidecar -----------------------------------------------------
@pytest.mark.parametrize("failure", ["http", "raise", "no_client"])
def test_a_lapsed_broker_login_is_recorded_and_the_next_good_run_heals_the_gap(broker, market, failure):
    """A failed minute-bar fetch is recorded in health.json as an error, beside
    the last minute the log holds, and adds no bars; the next good run fills
    every missed completed minute, in time order, with no minute written twice."""
    day = market.session_day()
    opened = _at(day, SB.SESSION_OPEN)
    first, lapsed, back = (opened + timedelta(minutes=m) for m in (30, 45, 60))
    pre_market = opened - timedelta(minutes=5)

    broker.tape = _tape(pre_market, first)
    assert SB.run(now=first) == 0
    before = SB.bars_path(day.isoformat()).read_text()
    assert len(before.splitlines()) == 30

    broker.tape, broker.fail = _tape(pre_market, lapsed), failure
    assert SB.run(now=lapsed) != 0
    health = json.loads(SB.health_path().read_text())
    assert health.get("error")
    assert health["last_bar_at"] == (first - timedelta(minutes=1)).isoformat()
    assert health["bars_on_disk"] == 30
    assert SB.bars_path(day.isoformat()).read_text() == before

    broker.tape, broker.fail = _tape(pre_market, back), None
    assert SB.run(now=back) == 0
    stamps = [json.loads(ln)["ts"] for ln in SB.bars_path(day.isoformat()).read_text().splitlines()]
    assert stamps == [(opened + timedelta(minutes=i)).isoformat() for i in range(60)]
    assert "error" not in json.loads(SB.health_path().read_text())


def test_a_backfilled_session_holds_exactly_its_regular_session_minutes(broker, market):
    """A past session fetched by day lands whole — every regular-session minute,
    nothing from before the open or after the close — and today's record is
    left alone."""
    today = market.session_day()
    past = today - timedelta(days=1)
    while past.weekday() > 4 or not market.full_session(past):
        past -= timedelta(days=1)
    broker.tape = (_tape(_at(past, time(8, 0)), _at(past, time(17, 0)))
                   + _tape(_at(today, SB.SESSION_OPEN), _at(today, time(10, 0))))

    assert SB.run(day=past.isoformat(), now=_at(today, time(11, 0))) == 0
    bars = SB.read_bars(past.isoformat())
    assert len(bars) == SB.BARS_PER_SESSION
    assert bars[0]["ts"] == _at(past, SB.SESSION_OPEN).isoformat()
    assert bars[-1]["ts"] == (_at(past, SB.SESSION_CLOSE) - timedelta(minutes=1)).isoformat()
    assert SB.read_bars(today.isoformat()) == []


# --- the model call -------------------------------------------------------------
class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _ClaudeCLI:
    """Stands in for subprocess.run: records each command, then answers as scripted."""

    def __init__(self):
        self.commands, self.kwargs = [], []
        self.answer = lambda cmd, kw: _Done(0, json.dumps({"result": json.dumps({"quiet": True})}))

    def __call__(self, cmd, **kw):
        self.commands.append(list(cmd))
        self.kwargs.append(kw)
        return self.answer(cmd, kw)


@pytest.fixture
def claude(monkeypatch, tmp_path):
    """The real call functions back from the suite-wide guard, with the
    subprocess faked and PATH emptied so nothing can reach a real binary."""
    cli = _ClaudeCLI()
    monkeypatch.setattr(SR.subprocess, "run", cli)
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries-here"))
    monkeypatch.setattr(SR, "call_the_model", _REAL_CALL_THE_MODEL)
    monkeypatch.setattr(B, "call_the_model_v2", _REAL_CALL_THE_MODEL_V2)
    return cli


def _raise(exc):
    def answer(cmd, kw):
        raise exc
    return answer


FAILURES = {
    "timeout": _raise(subprocess.TimeoutExpired("claude", SR.CALL_TIMEOUT_S)),
    "no_binary": _raise(FileNotFoundError("claude: command not found")),
    "non_zero_exit": lambda cmd, kw: _Done(1, "", "not logged in"),
    "unparseable": lambda cmd, kw: _Done(0, "a reply with no JSON object in it"),
}
FAILED_BECAUSE = {"timeout": "timeout", "no_binary": "command not found", "non_zero_exit": "not logged in"}
CALLS = {"v1": lambda prompt: SR.call_the_model(prompt, SR.PINNED_MODEL),
         "v2": lambda prompt: B.call_the_model_v2(prompt)}


def _value(cmd: list, flag: str):
    return cmd[cmd.index(flag) + 1]


def test_the_reader_grants_the_model_no_tools(claude):
    """The reader's model call grants no tools and no MCP servers, is bounded by
    a timeout, and both doctrines share one command line."""
    CALLS["v1"]("scene")
    CALLS["v2"]("scene")
    assert len(claude.commands) == 2
    for cmd, kw in zip(claude.commands, claude.kwargs):
        assert cmd[0] == "claude" and _value(cmd, "-p") == "scene"
        denied = cmd[cmd.index("--disallowedTools") + 1:]
        assert OUTSIDE_WORLD_TOOLS <= set(denied)
        assert not any(a.startswith("-") for a in denied)     # a flag after the list would be read as a tool
        assert "--strict-mcp-config" in cmd
        assert json.loads(_value(cmd, "--mcp-config")) == {"mcpServers": {}}
        assert not {"--allowedTools", "--allowed-tools", "--dangerously-skip-permissions"} & set(cmd)
        assert kw.get("timeout") and kw["timeout"] > 0
    v1, v2 = claude.commands
    assert _value(v1, "--append-system-prompt") == SR._DOCTRINE
    assert _value(v2, "--append-system-prompt") == B.DOCTRINE_V2
    assert _value(v2, "--model") == SR.PINNED_MODEL
    doctrine_at = v1.index("--append-system-prompt") + 1
    assert v1[:doctrine_at] + v1[doctrine_at + 1:] == v2[:doctrine_at] + v2[doctrine_at + 1:]


@pytest.mark.parametrize("call", sorted(CALLS))
@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_a_failed_model_call_comes_back_as_a_result_not_an_exception(claude, call, failure):
    """A model call that times out, cannot start, exits non-zero or replies
    without JSON returns no reading and says why — an error naming the failure,
    or, for a reply that will not parse, the reply itself; it never raises."""
    claude.answer = FAILURES[failure]
    obj, err, wall, raw = CALLS[call]("scene")
    assert obj is None
    if failure == "unparseable":
        assert raw == "a reply with no JSON object in it"
    else:
        assert err and FAILED_BECAUSE[failure] in err
    assert isinstance(wall, (int, float))
    assert len(claude.commands) == 1


@pytest.mark.parametrize("failure", ["timeout", "non_zero_exit", "unparseable"])
def test_a_failed_model_call_is_recorded_on_the_read_row(claude, market, failure):
    """When the live reader's model call fails, the read row still lands and
    carries the error; the reader does not crash."""
    now = market.at(_at(market.session_day(), time(11, 0)))
    day = now.date().isoformat()
    diary = SR._diary_dir() / f"{day}.jsonl"
    diary.parent.mkdir(parents=True, exist_ok=True)
    diary.write_text("".join(json.dumps(_diary_row_with_board(now - timedelta(minutes=m),
                                                              spot=1200.0 + m)) + "\n"
                             for m in (8, 6, 4, 2)))
    claude.answer = FAILURES[failure]

    assert SR.read_once(now=now) == 0
    assert len(claude.commands) == 1
    rows = [json.loads(ln) for ln in (SR._reads_dir() / f"{day}.jsonl").read_text().splitlines()]
    assert rows[-1].get("error")


# --- the board's command line ----------------------------------------------------
def test_board_replay_walks_a_recorded_day_without_calling_or_writing(tmp_path, monkeypatch, market):
    """`sndk_board.py --replay` walks a recorded day through the live gate
    without spending a model call or writing a record to any store; an
    unknown invocation does nothing and exits non-zero."""
    now = _at(market.session_day(), time(10, 0))
    day = now.date().isoformat()
    diary = SR._diary_dir() / f"{day}.jsonl"
    diary.parent.mkdir(parents=True, exist_ok=True)
    diary.write_text("".join(json.dumps(r) + "\n"
                             for r in mkrows(n=8, start=now - timedelta(minutes=14))))
    walked, real = [], B.replay_day

    def spy(d, **kw):
        walked.extend(real(d, **kw))
        return walked
    monkeypatch.setattr(B, "replay_day", spy)

    # records only: the percentile cache is keyed by its own session list and
    # re-validated on every read, so a replay may refresh it harmlessly
    def snapshot():
        return {p: p.read_bytes() for p in tmp_path.rglob("*.jsonl")}
    before = snapshot()
    assert B._main(["--replay", day]) == 0
    assert walked
    assert snapshot() == before
    assert B._main([]) != 0
    assert snapshot() == before
