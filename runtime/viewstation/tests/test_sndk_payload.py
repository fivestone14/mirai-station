"""The SNDK Payload tab's three contracts.

1. The route answers exactly one name. This was never authentication (the server
   is LAN-only and no-auth by choice, and the page's matching UI lock was removed
   08-22); the test pins that the check is one name, case/space-insensitive, and
   that the name is configurable.
2. The builder hands back the scene the READER itself would build for the newest
   live SNDK row — through sndk_read's own functions, never a copy of the logic —
   plus the user-message wrapper the scene rides in, and it ignores forced
   (off-hours --force) rows the way read_once does.
3. The wake-gate numbers the tab quotes on STEP 7 ARE the reader's constants —
   sr-7's freshness ceilings included, since they gate the payload the same way.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import server
import snapshot

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("permitted,qs,ok", [
    ("will", {"user": ["will"]}, True),
    ("will", {"user": [" Will "]}, True),
    ("will", {"user": ["WILL"]}, True),
    ("will", {"user": ["bob"]}, False),
    ("will", {"user": ["will2"]}, False),
    ("will", {"user": [""]}, False),
    ("will", {}, False),
    ("ada", {"user": ["ada"]}, True),       # the name is the configured one, not a literal
    ("ada", {"user": ["will"]}, False),
    ("", {"user": [""]}, False),            # no configured name locks everyone
    ("", {"user": ["will"]}, False),
])
def test_payload_lock_is_exactly_one_name(permitted, qs, ok, monkeypatch):
    monkeypatch.setattr(server, "_PAYLOAD_USER", permitted)
    assert server._payload_unlocked(qs) is ok


def _row(ts, spot, sigma=80.0, forced=False):
    r = {
        "ticker": "SNDK", "ts": ts.isoformat(), "spot": spot, "sigma": sigma,
        "prior_close": round(spot * 0.98, 2), "gamma_sign": "negative", "regime": "trending",
        "gex_views": {
            "front_dte": 2, "magnet": 1600.0,
            "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
            "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]],
        },
        "meta": {"expiries": [{"date": "2026-08-21", "dte": 2}]},
    }
    if forced:
        r["meta"]["forced"] = True
    return r


def _write_day(tmp_path, day, rows):
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_builder_hands_back_the_readers_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))      # sndk_read reads it per call
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    rows = [_row(t, 1580.0), _row(t.replace(minute=57), 1583.0), _row(t.replace(hour=13, minute=1), 1586.2),
            _row(t.replace(hour=13, minute=3), 1590.0, forced=True)]   # a forced row must NOT be the scene
    _write_day(tmp_path, "2026-08-19", rows)

    now = datetime(2026, 8, 19, 13, 2, tzinfo=ET)               # one minute after the scan → live
    d = snapshot.sndk_payload(now)

    assert d["session"] == "2026-08-19"
    assert d["row_ts"].startswith("2026-08-19T13:01")
    assert d["as_of"] == "live" and d["built_at"] == now.isoformat()
    assert d["scans_today"] == 3
    sc = d["scene"]
    # sr-8: `instrument` left the SCENE — one frozen string the model does not
    # need told ~190 times a day — but the phone masthead reads it, so it moved
    # to the WRAPPER, off the diary row that already carries it. The phone spec
    # forbids the view hardcoding a ticker (absent means show nothing, never a
    # literal "SNDK"), so the wrapper has to keep supplying one.
    assert "instrument" not in sc
    assert d["instrument"] == "SNDK"
    assert sc["price"]["live_spot"] == 1586.2
    assert sc["clock"]["minutes_to_close"] == 178 and sc["clock"]["front_expiry"] == {"days_to_expiry": 2, "expiry_date": "2026-08-21"}
    # strikes-1 (09-05): the scene handed back is the Strikes Payload; the old
    # magnet lives on the legacy Scene Payload beside it, labelled deprecated
    assert d["payload"] == "strikes" and d["payload_label"] == "Strikes Payload v1"
    assert "magnet" not in sc and "walls" not in sc
    assert d["legacy"]["status"] == "deprecated" and d["legacy"]["sent_to_model"] is False
    assert d["legacy"]["scene"]["magnet"]["top_strikes"][0]["strike"] == 1600.0
    import sndk_board as board
    import sndk_read as R      # on sys.path once the builder above has run
    # the era follows the document sent: the reader's current era on the Strikes
    # Payload, the legacy era on the scene kept beside it. Read off the reader so
    # an ordinary era bump does not break this test, but the two must differ.
    assert d["era"] == R.ERA and d["legacy"]["era"] == R.LEGACY_ERA and R.ERA != R.LEGACY_ERA
    assert d["gate_payload"]["magnet"] == 1600.0
    # the wrapper is the reader's own, byte for byte: read_once sends
    # board.prompt_v2(scene), so the tab must show exactly that string
    assert d["user_prompt"] == board.prompt_v2(sc)
    assert json.loads(d["user_prompt"].split("SCENE:\n", 1)[1]) == sc
    assert d["scene_chars"] == len(json.dumps(sc, default=str))


def test_builder_reverts_to_the_scene_payload_on_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SNDK_PAYLOAD", "scene")
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    rows = [_row(t, 1580.0), _row(t.replace(minute=57), 1583.0), _row(t.replace(hour=13, minute=1), 1586.2)]
    _write_day(tmp_path, "2026-08-19", rows)
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["payload"] == "scene" and d["legacy"] is None and d["gate_payload"] is None
    assert d["era"] == "obs-5"
    assert d["scene"]["magnet"]["top_strikes"][0]["strike"] == 1600.0


def test_payload_ships_the_wake_gate_the_reader_actually_uses(tmp_path, monkeypatch):
    """STEP 7's tooltip quotes these numbers in plain English. They must BE the
    reader's own constants: a page quoting a threshold sndk_read has stopped
    using is the doctrine-drifts-from-code trap, and it has bitten six times."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0)])
    gates = snapshot.sndk_payload(datetime(2026, 8, 19, 12, 56, tzinfo=ET))["gates"]

    import sndk_read as R      # on sys.path once the builder above has run
    assert gates == {"min_gap_min": R.MIN_GAP_MIN, "daily_cap": R.DAILY_CALL_CAP,
                     "stale_book_min": R.STALE_BOOK_MIN, "heartbeat_min": R.HEARTBEAT_MIN,
                     "move_minutes": R.MOVE_MINUTES, "iv_pp": R.WAKE_IV_PP,
                     "flip_sigma": R.WAKE_FLIP_SIGMA,
                     "confirm_books": R.WAKE_CONFIRM_BOOKS,
                     # 09-02: the gate card's last two literals, now read off the reader
                     "iv_median_books": R.WAKE_IV_MEDIAN_BOOKS,
                     "flip_cross_sigma": R.WAKE_FLIP_CROSS_SIGMA,
                     "max_book_age_min": R.MAX_BOOK_AGE_MIN,
                     "bars_stale_min": R.BAR_RECORD_STALE_MIN}
    assert not hasattr(R, "MAX_QUOTE_AGE_MIN"), (
        "a quote ceiling measured off row.ts is a ceiling on the scan cadence, "
        "not on quote freshness — the diary carries no quote clock")


def test_builder_falls_back_to_the_last_scan_after_hours(tmp_path, monkeypatch):
    """The cutover is the book's own ceiling, not a round number: a book the
    reader would still read is built against the wall clock, and one a tenth of
    a minute past it is built as of its scan, whole, rather than gutted by the
    freshness gate."""
    from datetime import timedelta
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 15, 58, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1590.0)])
    d = snapshot.sndk_payload(datetime(2026, 8, 20, 6, 30, tzinfo=ET))   # next morning, pre-open
    assert d["as_of"] == "last scan"
    assert d["built_at"].startswith("2026-08-19T15:58")
    assert d["scene"]["clock"]["minutes_to_close"] == 2       # built as of the scan, not a dead 0

    import sndk_read as R      # on sys.path once the builder above has run
    ceiling = t + timedelta(minutes=R.MAX_BOOK_AGE_MIN)
    assert snapshot.sndk_payload(ceiling)["as_of"] == "live"
    past = snapshot.sndk_payload(ceiling + timedelta(seconds=6))
    assert past["as_of"] == "last scan" and past["built_at"] == t.isoformat()


def test_builder_says_so_when_there_is_no_tape(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    (tmp_path / "sndk_reversion").mkdir()
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["scene"] is None and "no SNDK diary rows" in d["error"]


class _H:
    def __init__(self, **h): self.h = h
    def get(self, k): return self.h.get(k)


def test_forwarded_user_comes_from_the_front_door_only():
    assert server._forwarded_user(_H()) is None
    assert server._forwarded_user(_H(**{"X-Forwarded-User": " Will "})) == "will"
    assert server._forwarded_user(_H(**{"Remote-User": "bob"})) == "bob"
    assert server._forwarded_user(_H(**{"X-Forwarded-User": "   "})) is None


# --- side-1: the bar-anchored packet beside the scene -------------------------
def _write_bars(tmp_path, day, n=120, price=1580.0):
    """The minute sidecar's own file shape, written straight to disk — the tab
    reads it through sndk_read.minute_bars exactly as the reader does."""
    from datetime import timedelta
    d = tmp_path / "sndk_bars"
    d.mkdir(parents=True, exist_ok=True)
    open_at = datetime.fromisoformat(f"{day}T09:30:00-04:00")
    (d / f"{day}.jsonl").write_text("\n".join(json.dumps({
        "ts": (open_at + timedelta(minutes=i)).isoformat(),
        "open": price, "high": price + 1.0, "low": price - 1.0,
        "close": price, "volume": 1000.0}) for i in range(n)) + "\n")


def test_the_payload_carries_the_side_packet_beside_the_scene(tmp_path, monkeypatch):
    """side-1: a sibling of the scene, never a child. The wrapper's user_prompt
    and scene_chars are pinned to the scene alone, so a packet nested inside it
    would break both — and the model is not being asked to read this one."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(hour=13, minute=1), 1586.2)])
    _write_bars(tmp_path, "2026-08-19")
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))

    side = d["side"]
    assert side is not None and side["bars"]["count"] == 120
    assert "side" not in d["scene"]
    # the two contracts the scene wrapper still has to keep, unchanged
    assert json.loads(d["user_prompt"].split("SCENE:\n", 1)[1]) == d["scene"]
    assert d["scene_chars"] == len(json.dumps(d["scene"], default=str))
    # every check in the packet ran, and none of them failed
    assert side["integrity"] and all(c["status"] in ("pass", "warn")
                                     for c in side["integrity"])


def test_the_side_packet_costs_the_model_nothing_and_stays_inside_its_ceiling(
        tmp_path, monkeypatch):
    """This packet is never sent to a model, so its bytes are disk and display,
    not tokens — which is why it is allowed to be larger than the scene. What it
    is NOT allowed to do is grow without anyone noticing: measured over the seven
    recorded sessions on disk it builds at 5.5-6.0 KB compact, so the ceiling is
    8 KB and a build that passes it should be argued for, not absorbed."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1586.2)])
    _write_bars(tmp_path, "2026-08-19", n=390)
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    # the model's half of the call is untouched by the packet's existence
    assert json.loads(d["user_prompt"].split("SCENE:\n", 1)[1]) == d["scene"]
    # _side_packet swallows every error and returns None, and None serialises
    # to four bytes — so the ceiling means nothing unless a full packet was
    # built: every completed minute from 09:30 through 13:01 is 212 bars
    side = d["side"]
    assert side is not None and side["bars"]["count"] == 212
    assert len(json.dumps(side, separators=(",", ":"), default=str)) < 8192


def test_a_missing_bar_file_leaves_the_scene_untouched(tmp_path, monkeypatch):
    """No sidecar file is an ordinary state — the packet says it has no bars
    and the scene renders exactly as it did before this key existed."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1586.2)])
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["side"]["bars_seen"] == 0
    assert d["side"]["absent"][0]["path"] == "bars[]"
    assert d["scene"]["price"]["live_spot"] == 1586.2


# ---------------------------------------------------------------- strikes-1 (2026-09-05)
def test_builder_falls_back_when_the_board_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SNDK_PAYLOAD", raising=False)
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(minute=57), 1583.0), _row(t.replace(hour=13, minute=1), 1586.2)])
    import sndk_board
    monkeypatch.setattr(sndk_board, "build_scene_v2", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["payload"] == "scene" and d["legacy"] is None and "RuntimeError" in d["payload_label"]
    assert json.loads(d["user_prompt"].split("SCENE:\n", 1)[1]) == d["scene"]
    assert d["scene"]["magnet"]["top_strikes"][0]["strike"] == 1600.0


def test_pipeline_events_and_days_read_the_rows_own_clocks(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "STATE_DIR", tmp_path)
    day = "2026-08-19"
    t = datetime(2026, 8, 19, 9, 30, tzinfo=ET)
    rows = [_row(t.replace(minute=32), 1580.0), _row(t.replace(minute=34), 1581.0), _row(t.replace(minute=36), 1582.0, forced=True)]
    rows[0]["meta"]["book_asof"] = rows[0]["ts"]
    rows[1]["meta"]["book_asof"] = rows[0]["ts"]                          # a cached repeat: one distinct book
    _write_day(tmp_path, day, rows)
    (tmp_path / "sndk_bars").mkdir()
    (tmp_path / "sndk_bars" / f"{day}.jsonl").write_text("\n".join(
        json.dumps({"ts": t.replace(minute=30 + i).isoformat(), "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}) for i in range(5)) + "\n")
    (tmp_path / "sndk_reads").mkdir()
    (tmp_path / "sndk_reads" / f"{day}.jsonl").write_text(json.dumps(
        {"ts": t.replace(minute=32, second=30).isoformat(), "era": "strikes-1", "payload": "strikes", "wake": "first read",
         "model": "claude-sonnet-5", "wall_s": 4.5, "quiet": False, "error": None}) + "\n")
    ev = snapshot.pipeline_events(day)
    assert ev["scans"] == [2.0, 4.0] and ev["books"] == [2.0]             # the forced row is excluded, the repeat collapsed
    assert len(ev["bars"]) == 5 and ev["absent"] == []
    assert ev["reads"][0] == {"m": 2.5, "wake": "first read", "spoke": True, "wall": 4.5, "quiet": False, "err": False, "era": "strikes-1", "payload": "strikes"}
    assert snapshot.pipeline_days() == [day]
    assert snapshot.pipeline_events("2026-01-01")["absent"] == ["no diary rows for the day", "no minute bars on disk for the day", "no read rows for the day"]


def test_the_payload_tab_shows_the_sentence_still_on_screen(tmp_path, monkeypatch):
    """strikes-4: the tab rebuilds what the reader would send, so it carries
    said_then from the last row that still has a sentence, even when a later
    call's sentence was withheld."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(minute=57), 1583.0),
                                        _row(t.replace(hour=13, minute=1), 1586.2)])
    said, emptied = t.replace(minute=57), t.replace(minute=59)
    (tmp_path / "sndk_reads").mkdir()
    (tmp_path / "sndk_reads" / "2026-08-19.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ts": said.isoformat(), "wall_s": 5.0, "spot": 1583.0, "gate": {"spot": 1583.0},
         "reading": {"read": "1600 holds the most contracts."}, "reading_ts": said.isoformat()},
        {"ts": emptied.isoformat(), "wall_s": 6.0, "spot": 1584.0, "gate": {"spot": 1584.0},
         "reading": {"quiet": True, "abstain": "forced"}, "reading_ts": emptied.isoformat()},
    ]) + "\n")
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    slr = d["scene"]["context"]["since_last_read"]
    assert slr["said_then"] == "1600 holds the most contracts." and slr["said_at"] == "12:57"


def test_the_payload_tab_carries_the_day_summary_the_reader_would_send(tmp_path, monkeypatch):
    """strikes-6: the tab's Strikes Payload carries the `day` block, and grades
    only the readings the reader itself would grade — this era's calls."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(minute=57), 1583.0),
                                        _row(t.replace(hour=13, minute=1), 1586.2)])
    now = datetime(2026, 8, 19, 13, 2, tzinfo=ET)
    era = snapshot.sndk_payload(now)["era"]
    said = t.replace(minute=57)
    (tmp_path / "sndk_reads").mkdir(exist_ok=True)     # the first build already wrote its volume cache there

    def write(era_of_call):
        (tmp_path / "sndk_reads" / "2026-08-19.jsonl").write_text(json.dumps(
            {"ts": said.isoformat(), "era": era_of_call, "wall_s": 5.0, "spot": 1583.0, "gate": {"spot": 1583.0},
             "reading": {"read": "1600 holds the most contracts.",
                         "sides": {"above": {"heavy": 1600.0, "heavy_leads_on": ["contracts"]}}},
             "reading_ts": said.isoformat()}) + "\n")
    write(era)
    day = snapshot.sndk_payload(now)["scene"]["day"]
    assert any(c.get("strike") == 1600 for g in day["earlier_claims"] for c in g["claims"])
    write("an-older-era")
    assert "earlier_claims" not in (snapshot.sndk_payload(now)["scene"].get("day") or {})


# --- what each strike traded today, for the phone's bars ---------------------

# The 2026-09-16 15:10:21 book at six strikes: open interest and today's volume,
# calls then puts, as the scanner writes them into the diary.
_OI = {1500.0: (1699, 3911), 1510.0: (407, 821), 1520.0: (500, 750),
       1530.0: (654, 889), 1540.0: (565, 571), 1550.0: (1427, 1071)}
_VOL = {1500.0: (1118, 3861), 1510.0: (199, 615), 1520.0: (1282, 1596),
        1530.0: (3824, 3632), 1540.0: (2743, 2270), 1550.0: (2535, 1481)}


def _book_row(ts, spot):
    r = _row(ts, spot, sigma=65.82)
    r["gex_views"].update({
        "magnet": 1500.0, "mass_by_strike": [[k, c + p] for k, (c, p) in sorted(_OI.items())],
        "oi_side_by_strike": [[k, c, p] for k, (c, p) in sorted(_OI.items())],
        "vol_side_by_strike": [[k, c, p] for k, (c, p) in sorted(_VOL.items())]})
    r["meta"].update({"chain_spot": spot, "book_asof": ts.isoformat(),
                      "expiries": [{"date": "2026-09-18", "dte": 2}]})
    return r


def test_the_phone_is_sent_what_each_strike_traded_today(tmp_path, monkeypatch):
    """The chart's bars (2026-09-18) are the contracts traded today at each
    strike, vol_calls + vol_puts, read off the strike rows of the scene the
    phone is sent: page.js state() takes PAY.scene.strikes. The mockups were
    drawn off the model's payload store, not this, so this pins the payload the
    phone actually receives. The ladder's legacy scene has no strike rows.

    Absent the same way: before 09:45 a book's counts cannot be told from the
    prior session's, and the builder withholds both columns on every row and
    says so in strikes.absent (sndk_board's WITHHELD_UNPROVABLE). The phone
    draws that as no bars, never as yesterday's."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 9, 16, 15, 10, tzinfo=ET)
    _write_day(tmp_path, "2026-09-16", [_book_row(t - timedelta(minutes=2 * i), 1517.0 + i) for i in range(3, -1, -1)])
    strikes = snapshot.sndk_payload(t + timedelta(seconds=40))["scene"]["strikes"]
    traded = {r["strike"]: (r["vol_calls"], r["vol_puts"]) for r in strikes["rows"] if "vol_calls" in r}
    assert traded == {int(k): v for k, v in _VOL.items()}
    assert not any(a.startswith("volume") for a in strikes.get("absent") or [])
    # a strike the book's volume does not cover (1,600, on the net surface
    # only) rides with neither column, never a zero: the phone skips its bar
    assert [r["strike"] for r in strikes["rows"] if "vol_calls" not in r and "vol_puts" not in r] == [1600]

    early = datetime(2026, 9, 16, 9, 31, tzinfo=ET)
    _write_day(tmp_path, "2026-09-16", [_book_row(early, 1517.0)])
    strikes = snapshot.sndk_payload(early + timedelta(seconds=40))["scene"]["strikes"]
    assert strikes["rows"] and not any("vol_calls" in r or "vol_puts" in r for r in strikes["rows"])
    assert any(a.startswith("volume:") for a in strikes["absent"])


# --- reads_today: when the model spoke, on the display side of the fence ----

def _write_reads(root, day, rows):
    d = root / "sndk_reads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_reads_today_counts_what_the_model_said_not_what_the_scanner_wrote(tmp_path, monkeypatch):
    """A session writes ~190 rows carrying ~22 distinct readings forward, so a
    row-per-mark would draw the same reading nine times over. The distinct
    `reading_ts` is the utterance, and it is the key sndk_thread() already uses
    — a second key here is how two counts of "how many times it spoke" start
    disagreeing on the same screen."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_reads(tmp_path, "2026-09-16", [
        {"reading_ts": "2026-09-16T09:31:31-04:00", "spot": 1554.65},
        {"reading_ts": "2026-09-16T09:31:31-04:00", "spot": 1550.00},   # carried forward
        {"reading_ts": "2026-09-16T09:31:31-04:00", "spot": 1548.10},   # carried forward
        {"reading_ts": "2026-09-16T15:11:19-04:00", "spot": 1513.45},
    ])
    got = snapshot._reads_today("2026-09-16")
    assert [r["ts"] for r in got] == ["2026-09-16T09:31:31-04:00", "2026-09-16T15:11:19-04:00"]
    assert [r["spot"] for r in got] == [1554.65, 1513.45]
    assert len(json.dumps(got)) < 400, "the whole point is that this is small"


def test_reads_today_is_absent_rather_than_empty_when_the_model_never_spoke(tmp_path, monkeypatch):
    """Honest-absent. An empty list on the wire reads as "it looked and found
    nothing to say", which is a different fact from "it has not spoken yet" and
    from "this payload predates the field". All three must draw no marks, and
    none of them may draw a zero."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    assert snapshot._reads_today("2026-09-16") is None          # no file at all
    _write_reads(tmp_path, "2026-09-16", [{"ts": "2026-09-16T09:31:31-04:00", "spot": 1554.65}])
    assert snapshot._reads_today("2026-09-16") is None          # scanned, never spoke


def test_a_reading_with_no_price_beside_it_is_left_off_the_line(tmp_path, monkeypatch):
    """The mark's whole content is WHERE on the price line the model was
    looking. A reading whose row carries no spot cannot be placed, and placing
    it at a neighbour's price would be the chart inventing the one thing the
    mark exists to say."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_reads(tmp_path, "2026-09-16", [
        {"reading_ts": "2026-09-16T09:31:31-04:00", "spot": 1554.65},
        {"reading_ts": "2026-09-16T11:02:00-04:00"},
        {"reading_ts": "2026-09-16T12:02:00-04:00", "spot": None},
        {"reading_ts": "2026-09-16T13:02:00-04:00", "spot": True},      # a bool is not a price
        {"reading_ts": "2026-09-16T15:11:19-04:00", "spot": 1513.45},
    ])
    assert [r["spot"] for r in snapshot._reads_today("2026-09-16")] == [1554.65, 1513.45]


# --- the record under the phone's last-half-hour card ------------------------

def _hh_rows(day, moves, sigma=100.0, keep=lambda minute: True):
    """One session's diary: a scan every 2 minutes from 09:30 to 16:00, the
    price stepping by moves[i] dollars at each :00/:30 edge and flat between.
    Every edge has a scan on it, so grid half hour i moves by exactly moves[i].
    `keep` takes minutes since the open and drops the scans it refuses."""
    open_at = datetime.fromisoformat(f"{day}T09:30:00-04:00")
    px, rows = 1500.0, []
    for m in range(0, 391, 2):
        if m and m % 30 == 0:
            px += moves[m // 30 - 1]
        if keep(m):
            rows.append({"ticker": "SNDK", "ts": (open_at + timedelta(minutes=m)).isoformat(),
                         "spot": px, "sigma": sigma})
    return rows


_SEESAW = [20.0, -20.0] * 6 + [20.0]      # 13 half hours, each the other way from the last
_CLIMB = [40.0] * 13                      # 13 half hours, each the same way as the last


def test_the_record_counts_back_to_back_half_hours_in_each_sessions_own_sigma(tmp_path, monkeypatch):
    """Three sessions. 09-11 seesaws $20 a half hour on a $100 ruler, 0.20 of
    a day's move each; 09-14 steps $15 a half hour the same way on $100, 0.15;
    09-15 climbs $40 a half hour on an $800 ruler, 0.05. In DOLLARS the climbing
    day holds the biggest half hours; in each session's own sigma it holds the
    smallest, and the counts say which ruler was used.

    Usual is the median, 0.15, and a half hour exactly at it counts as bigger:
    that is the cut the card's "That was bigger than usual" is made on, so the
    sentence and the counts under it cannot disagree. Bigger is then 09-11's
    twelve pairs, every one of them the other way, and 09-14's twelve, every
    one the same way; no bigger is 09-15's twelve, the same way.

    The ruler is each session's MEDIAN sigma. 09-14's opens at $200 and $180
    before it settles at $100, and its half hours are still 0.15 of a day.

    Twelve pairs a session, not the ~180 a sliding window over the same scans
    would give: non-overlapping half hours on the :00/:30 grid, 09:30 to 16:00."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-09-11", _hh_rows("2026-09-11", _SEESAW, sigma=100.0))
    steps = _hh_rows("2026-09-14", [15.0] * 13, sigma=100.0)
    steps[0]["sigma"], steps[1]["sigma"] = 200.0, 180.0
    _write_day(tmp_path, "2026-09-14", steps)
    _write_day(tmp_path, "2026-09-15", _hh_rows("2026-09-15", _CLIMB, sigma=800.0))
    got = snapshot._earlier_half_hours("2026-09-16")
    assert got["usual_sigma"] == 0.15
    assert {k: got[k] for k in ("sessions", "first", "last", "half_hours", "pairs")} == \
        {"sessions": 3, "first": "2026-09-11", "last": "2026-09-15", "half_hours": 39, "pairs": 36}
    assert got["bigger"] == {"n": 24, "other_way": 12, "same_way": 12}
    assert got["no_bigger"] == {"n": 12, "other_way": 0, "same_way": 12}
    for side in ("bigger", "no_bigger"):
        assert got[side]["other_way"] + got[side]["same_way"] == got[side]["n"]
    assert got["bigger"]["n"] + got["no_bigger"]["n"] == got["pairs"]


def test_the_record_never_holds_the_session_on_screen(tmp_path, monkeypatch):
    """The card scores today's half hour against the record, so today cannot be
    in it — nor anything after it. The session on screen's own file, and a later
    one, are written with half hours that would move every count, and none do."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-09-14", _hh_rows("2026-09-14", _SEESAW))
    _write_day(tmp_path, "2026-09-15", _hh_rows("2026-09-15", _CLIMB, sigma=400.0))
    before = snapshot._earlier_half_hours("2026-09-16")
    monkeypatch.setattr(snapshot, "_HH_CACHE", {"key": None, "val": None})
    for day in ("2026-09-16", "2026-09-17"):
        _write_day(tmp_path, day, _hh_rows(day, [300.0] * 13))
    got = snapshot._earlier_half_hours("2026-09-16")
    assert got == before and got["last"] == "2026-09-15"
    # the next session on screen takes 09-16 in, and still not itself
    assert snapshot._earlier_half_hours("2026-09-17")["last"] == "2026-09-16"


@pytest.mark.parametrize("usable,counted", [(59, False), (60, True)])
def test_a_session_needs_sixty_usable_scans_to_be_in_the_record(tmp_path, monkeypatch, usable, counted):
    """Sessions with fewer than 60 usable scans are left out: 09-09 kept 14 and
    08-14 kept 25. Usable is what sndk_payload itself would read — SNDK, not a
    forced off-hours scan — with a time, a price and a ruler. 09-15 has all 196
    rows on disk, but only `usable` of them pass; the rest fail five different
    ways, and none of those five counts toward the sixty."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-09-14", _hh_rows("2026-09-14", _SEESAW))
    rows = _hh_rows("2026-09-15", _CLIMB, sigma=400.0)
    spoil = (lambda r: r.update(spot=None), lambda r: r.update(sigma=0),
             lambda r: r.update(ticker="SPX"), lambda r: r.update(meta={"forced": True}),
             lambda r: r.update(ts="not a time"))
    for i, r in enumerate(rows[usable:]):
        spoil[i % len(spoil)](r)
    _write_day(tmp_path, "2026-09-15", rows)
    got = snapshot._earlier_half_hours("2026-09-16")
    assert got["sessions"] == (2 if counted else 1)
    assert got["last"] == ("2026-09-15" if counted else "2026-09-14")


def test_an_edge_with_no_scan_near_it_leaves_its_half_hours_unmeasured(tmp_path, monkeypatch):
    """Each :00/:30 edge takes the nearest scan within 150 seconds. With the
    scans at 11:58, 12:00 and 12:02 gone, the nearest to noon is four minutes
    off, so noon has no price: the half hours either side of it are not
    measured and the three pairs that hold them are not counted. Nothing is
    interpolated across the hole."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-09-15",
               _hh_rows("2026-09-15", _SEESAW, keep=lambda m: abs(m - 150) > 2))
    got = snapshot._earlier_half_hours("2026-09-16")
    assert (got["half_hours"], got["pairs"]) == (11, 9)
    # and a scan inside the 150 seconds still prices the edge
    _write_day(tmp_path, "2026-09-15",
               _hh_rows("2026-09-15", _SEESAW, keep=lambda m: abs(m - 150) != 0))
    assert (snapshot._earlier_half_hours("2026-09-16")["half_hours"]) == 13


def test_the_record_is_absent_when_there_is_nothing_to_count(tmp_path, monkeypatch):
    """Honest-absent: None, never a record of zeros that the card would print as
    "So were 0 earlier half hours". No diary at all; only the session on
    screen; only a session short of sixty usable scans; and sixty scans that
    never span two back-to-back half hours, which is no pair to count."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    assert snapshot._earlier_half_hours("2026-09-16") is None
    _write_day(tmp_path, "2026-09-16", _hh_rows("2026-09-16", _SEESAW))
    assert snapshot._earlier_half_hours("2026-09-16") is None
    _write_day(tmp_path, "2026-09-15", _hh_rows("2026-09-15", _SEESAW)[:59])
    assert snapshot._earlier_half_hours("2026-09-16") is None
    open_at = datetime.fromisoformat("2026-09-15T09:30:00-04:00")
    _write_day(tmp_path, "2026-09-15", [
        {"ticker": "SNDK", "ts": (open_at + timedelta(seconds=30 * i)).isoformat(),
         "spot": 1500.0 + i, "sigma": 100.0} for i in range(61)])       # 09:30 to 10:00 only
    assert snapshot._earlier_half_hours("2026-09-16") is None


def test_the_record_is_kept_for_the_day_and_rebuilt_when_the_files_change(tmp_path, monkeypatch):
    """The record changes at most once a day, and the phone asks every minute.
    A second ask for the same session reads no file. The morning the next
    session is on screen, the one that closed yesterday is in the record. And a
    late write to an earlier file is not served from before it."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    reads = []
    real = snapshot._jsonl_rows
    monkeypatch.setattr(snapshot, "_jsonl_rows", lambda p: reads.append(p.name) or real(p))
    _write_day(tmp_path, "2026-09-14", _hh_rows("2026-09-14", _SEESAW))
    _write_day(tmp_path, "2026-09-15", _hh_rows("2026-09-15", _CLIMB, sigma=400.0))
    first = snapshot._earlier_half_hours("2026-09-16")
    assert sorted(reads) == ["2026-09-14.jsonl", "2026-09-15.jsonl"]
    reads.clear()
    assert snapshot._earlier_half_hours("2026-09-16") == first and reads == []

    # 09-16 closes; the next morning it is in the record
    _write_day(tmp_path, "2026-09-16", _hh_rows("2026-09-16", _SEESAW))
    kept = snapshot._earlier_half_hours("2026-09-17")
    assert (kept["sessions"], kept["last"]) == (3, "2026-09-16")

    # an earlier session rewritten: the answer is the one a cold start gives
    _write_day(tmp_path, "2026-09-14", _hh_rows("2026-09-14", _CLIMB, sigma=400.0))
    reads.clear()
    got = snapshot._earlier_half_hours("2026-09-17")
    assert "2026-09-14.jsonl" in reads and got != kept
    monkeypatch.setattr(snapshot, "_HH_CACHE", {"key": None, "val": None})
    assert got == snapshot._earlier_half_hours("2026-09-17")


def test_a_bad_line_in_an_earlier_diary_costs_that_line_alone(tmp_path, monkeypatch):
    """The record reads every earlier session's diary on the payload's own
    path, so one bad line in any of them is the whole payload's problem. A
    torn line was always skipped. A line that is JSON but not a row — null, a
    list, a string, a number — reached r.get and raised, and the route then
    answered with an error in place of the payload, the phone's and the
    desktop tab's, on every poll until the file was mended. It is skipped
    now, as sndk_read's own reader skips it, and the record is the one the
    clean rows give."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-08-17", _hh_rows("2026-08-17", _SEESAW))
    _write_day(tmp_path, "2026-08-18", _hh_rows("2026-08-18", _CLIMB, sigma=400.0))
    clean = snapshot._earlier_half_hours("2026-08-19")
    with open(tmp_path / "sndk_reversion" / "2026-08-17.jsonl", "a") as f:
        f.write('null\n[1, 2]\n"a row"\n42\n{"ticker": "SNDK", "ts": \n')
    assert snapshot._earlier_half_hours("2026-08-19") == clean
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(hour=13, minute=1), 1586.2)])
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["session"] == "2026-08-19" and d["earlier_half_hours"] == clean


def test_the_payload_carries_the_record_on_the_display_side(tmp_path, monkeypatch):
    """The phone reads the record off the payload wrapper, beside `reads_today`
    and `levels`. It is the record for the session the payload shows, and like
    them it is display only: it never reaches the model's message."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    _write_day(tmp_path, "2026-08-17", _hh_rows("2026-08-17", _SEESAW))
    _write_day(tmp_path, "2026-08-18", _hh_rows("2026-08-18", _CLIMB, sigma=400.0))
    t = datetime(2026, 8, 19, 12, 55, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1580.0), _row(t.replace(hour=13, minute=1), 1586.2)])
    d = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))
    assert d["session"] == "2026-08-19"
    rec = d["earlier_half_hours"]
    assert (rec["sessions"], rec["first"], rec["last"]) == (2, "2026-08-17", "2026-08-18")
    assert "earlier_half_hours" not in d["scene"] and "usual_sigma" not in d["user_prompt"]
