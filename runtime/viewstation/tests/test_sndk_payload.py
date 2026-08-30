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
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server
import snapshot

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("qs,ok", [
    ({"user": ["will"]}, True),
    ({"user": [" Will "]}, True),
    ({"user": ["WILL"]}, True),
    ({"user": ["bob"]}, False),
    ({"user": ["will2"]}, False),
    ({"user": [""]}, False),
    ({}, False),
])
def test_payload_lock_is_exactly_one_name(qs, ok, monkeypatch):
    monkeypatch.setattr(server, "_PAYLOAD_USER", "will")
    assert server._payload_unlocked(qs) is ok


def test_payload_lock_name_is_configurable(monkeypatch):
    monkeypatch.setattr(server, "_PAYLOAD_USER", "ada")
    assert server._payload_unlocked({"user": ["ada"]})
    assert not server._payload_unlocked({"user": ["will"]})


def test_payload_lock_empty_name_locks_everyone(monkeypatch):
    monkeypatch.setattr(server, "_PAYLOAD_USER", "")
    assert not server._payload_unlocked({"user": [""]})
    assert not server._payload_unlocked({"user": ["will"]})


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
    assert sc["instrument"] == "SNDK"
    assert sc["price"]["live_spot"] == 1586.2
    assert sc["clock"]["minutes_to_close"] == 178 and sc["clock"]["front_expiry"] == {"built_from": ["options_book"],
                                                                "days_to_expiry": 2,
                                                                "expiry_date": "2026-08-21"}
    assert sc["magnet"]["top_strikes"][0]["strike"] == 1600.0
    # the wrapper is the reader's own, byte for byte, with the same compact JSON inside
    assert d["user_prompt"].startswith("Read this scene cold and reply with the JSON object only.\n\nSCENE:\n")
    assert json.loads(d["user_prompt"].split("SCENE:\n", 1)[1]) == sc
    assert d["scene_chars"] == len(json.dumps(sc, default=str))


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
                     "price_sigma": R.WAKE_PRICE_SIGMA, "magnet_sigma": R.WAKE_MAGNET_SIGMA,
                     "max_book_age_min": R.MAX_BOOK_AGE_MIN}
    assert not hasattr(R, "MAX_QUOTE_AGE_MIN"), (
        "a quote ceiling measured off row.ts is a ceiling on the scan cadence, "
        "not on quote freshness — the diary carries no quote clock")


def test_builder_falls_back_to_the_last_scan_after_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 15, 58, tzinfo=ET)
    _write_day(tmp_path, "2026-08-19", [_row(t, 1590.0)])
    d = snapshot.sndk_payload(datetime(2026, 8, 20, 6, 30, tzinfo=ET))   # next morning, pre-open
    assert d["as_of"] == "last scan"
    assert d["built_at"].startswith("2026-08-19T15:58")
    assert d["scene"]["clock"]["minutes_to_close"] == 2       # built as of the scan, not a dead 0


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
