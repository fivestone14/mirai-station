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
from pathlib import Path
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
    """The phone reads the record off the payload wrapper, beside `instrument`.
    It is the record for the session the payload shows, and like `instrument`
    it is display only: it never reaches the model's message. The `levels`
    block that rode beside them fed only the phone's three-levels card and
    went with it (2026-09-19), rather than being built on every request for
    nothing to read."""
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
    assert "levels" not in d


# --- each strike's calls and puts at the latest reading, for the paler ends --

_SINCE = json.loads((Path(__file__).parent / "since_read_2026-09-15_17.json").read_text())


def _since(tmp_path, monkeypatch, case, board, strikes):
    """_since_read on a copied case of since_read_2026-09-15_17.json, cut to
    what the station held at the scan `board` (HH:MM:SS): the diary and read
    rows written by then, and the strike rows the phone was sent. The state
    folder is empty, so no earlier session is on disk."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(str(snapshot._SNDK_PRO_DIR))
    import sndk_board
    import sndk_read
    rows = [r for r in case["diary"] if r["ts"][11:19] <= board]
    reads = [r for r in case["reads"] if r["ts"][11:19] <= board]
    now = datetime.fromisoformat(rows[-1]["ts"]) + timedelta(seconds=40)
    return snapshot._since_read(sndk_read, sndk_board, rows, reads, {"strikes": {"rows": strikes}}, now)


def test_the_count_behind_the_reading_is_the_one_the_model_read(tmp_path, monkeypatch):
    """The 14:18:47 reading of 2026-09-16 was written from the 14:14:40 book,
    and the 14:18:46 book landed a second before its row was stamped. The
    model's change cell counts from the last book before that stamp, so from
    14:22 to 14:41 it gave 1,500 +16 puts since the reading against a true +44
    at 14:22:54 (CPB-SPEC.md 1.3). The field names the book on the reading's
    own call row, and the phone's now minus it is the true +44."""
    case = _SINCE["late_book"]
    call = next(r for r in case["reads"] if r["wall_s"] is not None)
    assert call["ts"][11:19] == "14:18:47" and call["book_asof"][11:19] == "14:14:40"
    assert any("14:14:40" < r["meta"]["book_asof"][11:19] and r["ts"] < call["ts"] for r in case["diary"]), \
        "no book landed while the model wrote; this proves nothing"
    got = _since(tmp_path, monkeypatch, case, "14:22:54", case["strikes"])
    assert (got["read_at"], got["book_at"]) == (call["reading_ts"], call["book_asof"])
    then = {k: (c, p) for k, c, p in got["rows"]}
    now = {r["strike"]: (r["vol_calls"], r["vol_puts"]) for r in case["strikes"]}
    assert then[1500] == (909, 3151) and now[1500][1] - then[1500][1] == 44
    # every strike sent is a listed one with both columns, at most once
    assert len(then) == len(got["rows"]) and set(then) <= set(now)


def test_a_book_is_every_strike_its_scans_kept(tmp_path, monkeypatch):
    """Two scans that read one book each keep the strikes near their own price,
    and agree on every strike they share: on 40 to 56 books a day of
    2026-09-15..17 the two kept different strikes. The 15:11:19 reading was
    written from the 15:08:17 book; 1,430 is on its 15:10:21 scan only, at 6
    calls and 351 puts. Read off one scan the strike had no count at the
    reading, and its bar no paler end."""
    case = _SINCE["shared_book"]
    scans = [{k: (c, p) for k, c, p in r["gex_views"]["vol_side_by_strike"]}
             for r in case["diary"] if r["meta"]["book_asof"][11:19] == "15:08:17"]
    assert len(scans) == 2 and 1430.0 not in scans[0] and scans[1][1430.0] == (6, 351)
    assert all(scans[0][k] == scans[1][k] for k in set(scans[0]) & set(scans[1]))
    got = _since(tmp_path, monkeypatch, case, "15:12:24", case["strikes"])
    assert got["book_at"][11:19] == "15:08:17" and [1430, 6, 351] in got["rows"]


def test_a_count_the_vendor_served_stale_is_left_out(tmp_path, monkeypatch):
    """2026-09-17: the 11:37:26 reading was written from the 11:34:30 book,
    whose 2,859 calls at 1,600 the vendor had served since 11:22 against 5,230
    in the 11:18:01 book. carried_books judges whole books, and this one
    passed. The model's change cell then gave 1,600 +2,912 calls since the
    reading from 11:38 to 11:46, contracts that never traded. A strike whose
    count at the reading, or now, is below its count in any of the five books
    before is left out: at 11:34:30 every listed strike's calls were below
    their 11:18:01 count, so nothing is sent and no bar gets a paler end. The
    11:18:01 book is itself withheld by then, for counting more than a later
    book; the guard still reads it, since that is the step backwards it looks
    for. Without the guard 1,600 would ship at 2,859."""
    case = _SINCE["stale_count"]
    book = lambda t: next({k: (c, p) for k, c, p in r["gex_views"]["vol_side_by_strike"]}
                          for r in case["diary"] if r["meta"]["book_asof"][11:19] == t)
    assert book("11:34:30")[1600.0][0] == 2859 and book("11:18:01")[1600.0][0] == 5230
    for board, strikes in case["boards"].items():
        got = _since(tmp_path, monkeypatch, case, board, strikes)
        assert got["book_at"][11:19] == "11:34:30" and got["rows"] == [], board
    now = {r["strike"]: r["vol_calls"] for r in case["boards"]["11:38:37"]}
    assert now[1600] - 2859 == 2912
    monkeypatch.setattr(snapshot, "_SINCE_READ_BOOKS", 0)
    got = _since(tmp_path, monkeypatch, case, "11:38:37", case["boards"]["11:38:37"])
    assert [1600, 2859, 2638] in got["rows"], "the guard is not what leaves 1,600 out; this proves nothing"


def test_the_reading_is_the_newest_of_the_day_as_the_card_chooses_it(tmp_path, monkeypatch):
    """The card picks the newest reading_ts among the read rows it fetched,
    and so does the field, over the whole day. On 2026-09-15 the 11:31:37
    call wrote points and no sentence, and the rows after it carried the 11:11
    sentence forward with its stamp. At 13:23 the 11:31:37 row had left the
    40 rows the phone fetches, so the card showed 11:11 while the field names
    11:31:37 (CPB-SPEC.md 5). The phone draws paler ends only for the reading
    on its card, so there it draws none: absent, never counted from the wrong
    reading."""
    case = _SINCE["older_card"]
    got = _since(tmp_path, monkeypatch, case, "13:23:21", case["strikes"])
    assert got["read_at"][11:19] == "11:31:37" and got["book_at"][11:19] == "11:31:08" and got["rows"]
    card = max((r for r in case["reads"][-40:] if r["reading"]), key=lambda r: r["reading_ts"])["reading_ts"]
    assert card[11:19] == "11:11:42" and card != got["read_at"]


def _vol_row(ts, vol):
    """_book_row with its own volume by strike, {strike: (calls, puts)}."""
    r = _book_row(ts, 1517.0)
    r["gex_views"]["vol_side_by_strike"] = [[k, c, p] for k, (c, p) in sorted(vol.items())]
    return r


def _read_row(ts, book, reading):
    """A read row that spent a call at `ts`, written from `book`."""
    return {"ts": ts.isoformat(), "reading_ts": ts.isoformat(), "wall_s": 20.0,
            "book_asof": book.isoformat(), "reading": reading}


def _write_reads(tmp_path, day, rows):
    d = tmp_path / "sndk_reads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_payload_carries_the_count_at_the_reading_or_says_why_not(tmp_path, monkeypatch):
    """The field rides the payload wrapper beside `earlier_half_hours`, for the
    phone's chart only: never in the scene, never in the model's message.
    [strike, calls, puts] for each listed strike with both columns, in the
    book the latest reading was written from; or `unavailable`, with the
    reading and its book once there is one:
      - no read row today carries a reading: no_reading_yet;
      - the reading's own book is still the newest: nothing was measured since;
      - its call row names a book the diary does not hold: no count at it;
      - the book at the reading, or now, still carries the prior session's
        counts (before 09:45 nothing proves otherwise): withheld, as the
        table withholds it."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    day, t = "2026-09-16", datetime(2026, 9, 16, 11, 0, tzinfo=ET)
    books = [t + timedelta(minutes=4 * i) for i in range(4)]
    vols = [{k: (c + 10 * i, p + 20 * i) for k, (c, p) in _VOL.items()} for i in range(4)]
    _write_day(tmp_path, day, [_vol_row(b, v) for b, v in zip(books, vols)])
    now = books[-1] + timedelta(seconds=40)
    assert snapshot.sndk_payload(now)["since_read"] == {"unavailable": "no_reading_yet"}
    said = books[1] + timedelta(seconds=30)
    _write_reads(tmp_path, day, [
        _read_row(said, books[1], {"read": "1,530 traded the most."}),
        {"ts": (said + timedelta(minutes=2)).isoformat(), "reading_ts": said.isoformat(), "wall_s": None,
         "book_asof": books[2].isoformat(), "reading": {"read": "1,530 traded the most."}},
        {"ts": (said + timedelta(minutes=4)).isoformat(), "reading_ts": None, "reading": None}])
    d = snapshot.sndk_payload(now)
    sr = d["since_read"]
    assert (sr["read_at"], sr["book_at"]) == (said.isoformat(), books[1].isoformat())
    listed = [r["strike"] for r in d["scene"]["strikes"]["rows"] if r.get("vol_calls") is not None]
    assert sr["rows"] == [[k, vols[1][k][0], vols[1][k][1]] for k in listed]
    assert "since_read" not in d["scene"] and "since_read" not in d["user_prompt"]
    # a quiet reading is a reading, and the newest one decides
    _write_reads(tmp_path, day, [_read_row(said, books[1], {"read": "1,530 traded the most."}),
                                 _read_row(books[3] + timedelta(seconds=20), books[3], {"quiet": True})])
    assert snapshot.sndk_payload(now)["since_read"] == {
        "read_at": (books[3] + timedelta(seconds=20)).isoformat(), "book_at": books[3].isoformat(),
        "unavailable": "no_new_book_since_the_reading"}
    _write_reads(tmp_path, day, [_read_row(said, books[1] - timedelta(minutes=1), {"points": []})])
    assert snapshot.sndk_payload(now)["since_read"]["unavailable"] == "no_count_at_the_reading"
    early = [datetime(2026, 9, 16, 9, 31, tzinfo=ET) + timedelta(minutes=4 * i) for i in range(3)]
    _write_day(tmp_path, day, [_vol_row(b, _VOL) for b in early])
    _write_reads(tmp_path, day, [_read_row(early[1] + timedelta(seconds=30), early[1], {"read": "x"})])
    assert snapshot.sndk_payload(early[-1] + timedelta(seconds=40))["since_read"]["unavailable"] \
        == "a_book_carried_prior_session_volume"


def test_a_strike_is_left_out_for_five_books_after_its_count_went_back(tmp_path, monkeypatch):
    """The guard looks back five distinct books, about twenty minutes, not over
    the whole day: a running maximum would blank a strike the vendor revised
    down for the rest of the session (473 rows over 2026-09-15..17, CPB-SPEC.md
    1.4), and five books catch 09-17's bounce and let a revision recover. The
    day's opening books, withheld for the prior session's counts, are not
    looked back at: yesterday's whole day at a strike is more than this
    morning's, and every strike would be left out until they aged past.

    1,530's count is revised from 4,000 calls to 3,800 in the book after the
    10:04 one. Read from the next book, and from each until five books have
    followed the 4,000, 1,530 is left out; from the sixth, it is sent again.
    Every other strike is sent throughout, whatever the opening books held."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    day = "2026-09-16"
    opening = [datetime(2026, 9, 16, 9, 30, tzinfo=ET) + timedelta(minutes=4 * i) for i in range(4)]
    books = [datetime(2026, 9, 16, 10, 0, tzinfo=ET) + timedelta(minutes=4 * i) for i in range(10)]
    big = {k: (c * 9, p * 9) for k, (c, p) in _VOL.items()}
    vol = lambda i: {**{k: (c + 10 * i, p + 10 * i) for k, (c, p) in _VOL.items()},
                     1530.0: ((4000 if i == 1 else 3800) + 10 * i, 3632 + 10 * i)}
    _write_day(tmp_path, day, [_vol_row(b, big) for b in opening] + [_vol_row(b, vol(i)) for i, b in enumerate(books)])
    sent = []
    for i in range(2, 9):
        rows = [_vol_row(b, big) for b in opening] + [_vol_row(b, vol(j)) for j, b in enumerate(books[:i + 2])]
        _write_day(tmp_path, day, rows)
        _write_reads(tmp_path, day, [_read_row(books[i] + timedelta(seconds=30), books[i], {"read": "x"})])
        sr = snapshot.sndk_payload(books[i + 1] + timedelta(seconds=40))["since_read"]
        ks = [r[0] for r in sr["rows"]]
        assert set(ks) >= {1500, 1510, 1520, 1540, 1550}, i
        sent.append(1530 in ks)
    assert sent == [False, False, False, False, False, True, True]
