"""Tests for the gex-only ALERT BELL (intraday/gex_alerts.py) — the ported
wall-breach re-dive + paper-fire pushes + EOD mood scoring."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from watch.intraday import gex_alerts, macro_mood

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 6, 13, 0, tzinfo=ET)          # a Monday, mid-session
NOW_EOD = datetime(2026, 7, 6, 16, 5, tzinfo=ET)
DAY = "2026-07-06"


def _write_lens_rows(state_dir: Path, rows):
    d = state_dir / "reversion"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{DAY}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _row(ticker="SPX", spot=7500.0, cw=7520.0, pw=7400.0, sigma=50.0):
    return {"ticker": ticker, "ts": f"{DAY}T10:00:00-04:00", "spot": spot,
            "call_wall": cw, "put_wall": pw, "sigma": sigma}


def _stand_in_rows(start, now, step_min=4, source="spy_proxy\u00d710.0348"):
    """SPX rows on the SPY stand-in book every step_min from start, the last
    written at now (this tick)."""
    stamps = []
    t = start
    while t < now:
        stamps.append(t)
        t += timedelta(minutes=step_min)
    stamps.append(now)
    return [{**_row(), "ts": t.isoformat(), "gex_source": source} for t in stamps]


def _options_data(state_dir):
    return json.loads(gex_alerts._options_data_path(Path(state_dir)).read_text())


def _write_expectation(state_dir: Path, direction=0.5):
    d = state_dir / "market_expectation"
    d.mkdir(parents=True, exist_ok=True)
    exp = {"date": DAY, "overall": {"direction": direction, "magnitude": 0.5,
                                    "confidence": 0.8}, "sectors": {}, "reasoning": "test"}
    (d / f"{DAY}.json").write_text(json.dumps(exp))


def _write_fires(skill_dir: Path, fires):
    d = skill_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    hb = {"ts": f"{DAY}T10:00:00-04:00", "fires": fires}
    (d / f"{DAY}.jsonl").write_text(json.dumps(hb) + "\n")


class TestGexAlerts(unittest.TestCase):
    def _run(self, state_dir, skill_dir=None, now=NOW, redive="unset"):
        import watch.paths as paths
        sent, dives = [], []
        old_skill = paths.LEFT_EYE_SKILL
        if skill_dir is not None:
            paths.LEFT_EYE_SKILL = Path(skill_dir)
        try:
            provider = (lambda exp, ctx: dives.append(ctx)) if redive == "unset" else redive
            out = gex_alerts.run(now, state_dir=Path(state_dir),
                                 redive_provider=provider,
                                 channel=sent.append)
        finally:
            paths.LEFT_EYE_SKILL = old_skill
        return out, sent, dives

    def test_fire_pushed_once_ever(self):
        with TemporaryDirectory() as td, TemporaryDirectory() as sk:
            _write_fires(Path(sk), [{"ts": f"{DAY}T10:00:00", "ticker": "SPX",
                                     "direction": "call", "side": "LONG",
                                     "magnet": 7500.0, "gap_stretch": -1.2}])
            out1, sent1, _ = self._run(td, sk)
            out2, sent2, _ = self._run(td, sk)
            self.assertEqual(out1["pushed"], 1)
            self.assertEqual(sum("paper" in s for s in sent1), 1)
            self.assertEqual(out2["pushed"], 0)          # dedup across runs

    def test_breach_fires_redive_and_cooldown_suppresses(self):
        with TemporaryDirectory() as td:
            _write_expectation(Path(td))
            # The engine records the crossing on the row and has already re-placed
            # the call wall outward past spot, so the event is the only breach.
            r = _row(spot=7580.0, cw=7620.0)
            r["wall_breach"] = {"side": "call", "wall": 7520.0, "spot": 7580.0,
                                "overshoot_sigma": 1.2}
            _write_lens_rows(Path(td), [r])
            out1, sent1, dives1 = self._run(td)
            self.assertEqual(out1["breaches"], 1)
            self.assertEqual([(d["wall"], d["direction"]) for d in dives1], [(7520, 1)])
            self.assertTrue(any("SPX broke its 7520 wall" in s for s in sent1))
            out2, sent2, dives2 = self._run(td)          # same tick minutes → cooldown
            self.assertEqual(len(dives2), 0)
            self.assertFalse(any("wall" in s for s in sent2))

    def test_no_breach_inside_walls(self):
        with TemporaryDirectory() as td:
            _write_expectation(Path(td))
            _write_lens_rows(Path(td), [_row(spot=7500.0)])
            out, _, dives = self._run(td)
            self.assertEqual(out["breaches"], 0)
            self.assertEqual(len(dives), 0)

    def test_eod_scores_reliability_once(self):
        with TemporaryDirectory() as td:
            _write_expectation(Path(td), direction=0.5)   # bullish call
            _write_lens_rows(Path(td), [_row(spot=7450.0),
                                        _row(spot=7530.0)])  # day closed up
            out1, _, _ = self._run(td, now=NOW_EOD)
            self.assertTrue(out1["eod_scored"])
            p = macro_mood.read_reliability(Path(td))
            self.assertEqual((p.hits, p.misses), (1.0, 0.0))   # bullish call, up day: a hit
            out2, _, _ = self._run(td, now=NOW_EOD)
            self.assertFalse(out2["eod_scored"])          # idempotent per day



class TestFeedHealthSiren(unittest.TestCase):
    """Pass 4 (2026-07-13) — the 0DTE-discovery outage logged `no_zero_dte`
    honestly on every live row and paged no one. A FRESH diary row (the scanner
    is in-session right now) carrying a degraded-feed fact must push, once per
    condition per day; stale rows (after hours) must stay silent."""

    def _run(self, state_dir, now=NOW):
        sent = []
        out = gex_alerts.run(now, state_dir=Path(state_dir),
                             redive_provider=lambda exp, ctx: None,
                             channel=sent.append)
        return out, sent

    @staticmethod
    def _fresh_row(**kw):
        r = _row(**kw)
        r["ts"] = NOW.isoformat()                 # written this tick → in-session
        return r

    def test_no_zero_dte_on_fresh_row_pages_once_per_day(self):
        with TemporaryDirectory() as td:
            r = self._fresh_row()
            r["native_coverage"] = {"no_zero_dte": True, "zero_dte_dead": False}
            r["gex_source"] = "native"
            _write_lens_rows(Path(td), [r])
            out1, sent1 = self._run(td)
            self.assertEqual(out1["feed_sirens"], 1)
            self.assertTrue(any("0DTE" in s for s in sent1))
            out2, sent2 = self._run(td)           # same day, same condition → dedup
            self.assertEqual(out2["feed_sirens"], 0)

    def test_stale_row_after_close_is_silent(self):
        with TemporaryDirectory() as td:
            r = _row()                            # fixture ts = 10:00
            r["native_coverage"] = {"no_zero_dte": True}
            _write_lens_rows(Path(td), [r])
            out, sent = self._run(td, now=NOW_EOD)   # 16:05 — market closed
            self.assertEqual(out["feed_sirens"], 0)

    def test_stale_row_mid_session_pages_scanner_silent_once(self):
        with TemporaryDirectory() as td:
            r = _row()                            # ts = 10:00, NOW = 13:00 → 180 min stale
            _write_lens_rows(Path(td), [r])
            out1, sent1 = self._run(td)           # live market + silent scanner → page
            self.assertEqual(out1["feed_sirens"], 1)
            self.assertTrue(any("scanner silent" in s for s in sent1))
            out2, sent2 = self._run(td)           # once per day
            self.assertEqual(out2["feed_sirens"], 0)

    def test_empty_diary_mid_session_pages_scanner_silent(self):
        with TemporaryDirectory() as td:          # scanner died before its first row
            out, sent = self._run(td)
            self.assertEqual(out["feed_sirens"], 1)
            self.assertTrue(any("scanner silent" in s for s in sent))

    def test_healthy_fresh_row_is_silent(self):
        with TemporaryDirectory() as td:
            r = self._fresh_row()
            r["native_coverage"] = {"no_zero_dte": False, "zero_dte_dead": False}
            r["gex_source"] = "native"
            _write_lens_rows(Path(td), [r])
            out, _ = self._run(td)
            self.assertEqual(out["feed_sirens"], 0)

    def test_dead_book_pages_at_once_and_the_stand_in_book_waits(self):
        with TemporaryDirectory() as td:
            r = self._fresh_row()
            r["native_coverage"] = {"zero_dte_dead": True}
            r["gex_source"] = "spy_proxy×10.0348"   # live vocabulary: per-tick rescale
            _write_lens_rows(Path(td), [r])
            out, sent = self._run(td)
            self.assertEqual(out["feed_sirens"], 1)
            self.assertTrue(any("half-dead" in s for s in sent))
            self.assertFalse(any("Options data" in s for s in sent))

    # --- the options provider's outage (2026-10-08) --------------------------
    # On 10-06..08 the provider sent empty SPX books; the scanner ran on the SPY
    # stand-in and 14 of the 79 code questions went blank. The phone got a
    # "gravity feed degraded: source:spy_proxy" page at once and "options flow
    # dead" at 11:00, neither saying what was lost or whose problem it was.

    def test_the_stand_in_book_pages_after_fifteen_minutes_straight(self):
        start = NOW.replace(hour=9, minute=31)
        with TemporaryDirectory() as td:
            _write_lens_rows(Path(td), _stand_in_rows(start, start + timedelta(minutes=12)))
            out, sent = self._run(td, now=start + timedelta(minutes=12))
            self.assertEqual((out["feed_sirens"], sent), (0, []))
            _write_lens_rows(Path(td), _stand_in_rows(start, start + timedelta(minutes=16)))
            out, sent = self._run(td, now=start + timedelta(minutes=16))
            self.assertEqual(out["feed_sirens"], 1)
            self.assertEqual(sent, [
                "🩺 Options data down — 14 of 79 SPX questions are blank. The options provider "
                "has sent empty books for 16 min. The call still runs on the other 65. "
                "Nothing to do on our side."])
            # the rescale drifts every tick (11 distinct values on 07-10): one page an outage
            _write_lens_rows(Path(td), _stand_in_rows(start, start + timedelta(minutes=20),
                                                      source="spy_proxy×10.0351"))
            out, sent = self._run(td, now=start + timedelta(minutes=20))
            self.assertEqual((out["feed_sirens"], sent), (0, []))

    def test_the_wait_is_measured_from_the_outages_first_stand_in_row(self):
        """A morning on the native book, then the stand-in from 10:26: the clock
        starts at 10:26, not at the day's first row and not at this tick."""
        native = [{**_row(), "ts": (NOW.replace(hour=9, minute=30) + timedelta(minutes=i)).isoformat(),
                   "gex_source": "native"} for i in range(0, 50, 4)]
        start = NOW.replace(hour=10, minute=26)
        with TemporaryDirectory() as td:
            _write_lens_rows(Path(td), native + _stand_in_rows(start, start + timedelta(minutes=14)))
            self.assertEqual(self._run(td, now=start + timedelta(minutes=14))[0]["feed_sirens"], 0)
            _write_lens_rows(Path(td), native + _stand_in_rows(start, start + timedelta(minutes=15)))
            self.assertEqual(self._run(td, now=start + timedelta(minutes=15))[0]["feed_sirens"], 1)

    def test_the_all_clear_says_all_79_are_back_and_how_long_it_was_down(self):
        start, healed = NOW.replace(hour=9, minute=31), NOW.replace(hour=14, minute=23)
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(start, start + timedelta(minutes=16))
            _write_lens_rows(Path(td), rows)
            self.assertEqual(self._run(td, now=start + timedelta(minutes=16))[0]["feed_sirens"], 1)
            back = {**_row(), "ts": healed.isoformat(), "gex_source": "native"}   # the chain came back
            _write_lens_rows(Path(td), rows + [back])
            out, sent = self._run(td, now=healed)
            self.assertEqual(out["feed_recoveries"], 1)
            self.assertEqual(sent, ["🩺 Options data back — all 79 questions answering again. Down 4 h 52 min."])

    def test_an_outage_shorter_than_the_wait_never_pages_either_way(self):
        start = NOW.replace(hour=9, minute=31)
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(start, start + timedelta(minutes=8))
            _write_lens_rows(Path(td), rows)
            self._run(td, now=start + timedelta(minutes=8))
            back = {**_row(), "ts": (start + timedelta(minutes=12)).isoformat(), "gex_source": "native"}
            _write_lens_rows(Path(td), rows + [back])
            out, sent = self._run(td, now=start + timedelta(minutes=12))
            self.assertEqual((out["feed_sirens"], out["feed_recoveries"], sent), (0, 0, []))
            self.assertFalse(_options_data(td)["down"])
            self.assertIsNone(_options_data(td)["back_at"])

    def test_the_all_clear_is_sent_once_not_every_healthy_tick(self):
        start = NOW - timedelta(minutes=20)
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(start, NOW)
            _write_lens_rows(Path(td), rows)
            self._run(td)
            later = NOW + timedelta(minutes=4)
            rows.append({**_row(), "ts": later.isoformat(), "gex_source": "native"})
            _write_lens_rows(Path(td), rows)
            self._run(td, now=later)               # the recovery
            out3, sent3 = self._run(td, now=later)   # and every tick after it
            self.assertEqual(out3["feed_recoveries"], 0)
            self.assertEqual(sent3, [])

    def test_a_second_outage_the_same_day_pages_again(self):
        """The whole point of clearing the flag. Before this, a feed that broke
        at 09:37, healed at 11:00 and broke again at 14:00 paged for the morning
        and stayed mute all afternoon."""
        first, healed, second = (NOW.replace(hour=9, minute=37), NOW.replace(hour=11, minute=0),
                                 NOW.replace(hour=14, minute=0))
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(first, first + timedelta(minutes=16))
            _write_lens_rows(Path(td), rows)
            self.assertEqual(self._run(td, now=first + timedelta(minutes=16))[0]["feed_sirens"], 1)
            rows.append({**_row(), "ts": healed.isoformat(), "gex_source": "native"})
            _write_lens_rows(Path(td), rows)
            self.assertEqual(self._run(td, now=healed)[0]["feed_recoveries"], 1)
            rows += _stand_in_rows(second, second + timedelta(minutes=15), source="spy_proxy×10.0402")
            _write_lens_rows(Path(td), rows)
            out, sent = self._run(td, now=second + timedelta(minutes=15))
            self.assertEqual(out["feed_sirens"], 1)
            self.assertTrue(any("empty books for 15 min" in s for s in sent))  # timed from 14:00, not 09:37

    def test_flow_dead_keeps_quiet_through_a_stand_in_outage_and_after_it(self):
        """10-08: the stand-in book from 09:30, back at 14:20 on a flow-blind row.
        One page for the outage, one for its end; no "flow dead" in the wait,
        none while it stands, and none on the flow-blind row it came back on."""
        start = NOW.replace(hour=9, minute=30)
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(start, NOW.replace(hour=9, minute=42))
            _write_lens_rows(Path(td), rows)
            self.assertEqual(self._run(td, now=NOW.replace(hour=9, minute=42))[1], [])
            rows = _stand_in_rows(start, NOW.replace(hour=11, minute=30))
            _write_lens_rows(Path(td), rows)
            _, sent = self._run(td, now=NOW.replace(hour=11, minute=30))
            self.assertEqual(len(sent), 1)
            self.assertIn("Options data down", sent[0])
            back = NOW.replace(hour=14, minute=20)
            rows = _stand_in_rows(start, back - timedelta(minutes=4))
            rows.append({**_row(), "ts": back.isoformat(), "gex_source": "native"})   # no gex_theta: flow-blind
            _write_lens_rows(Path(td), rows)
            out, sent = self._run(td, now=back)
            self.assertEqual(len(sent), 1)
            self.assertIn("Options data back", sent[0])
            self.assertNotIn("flow_dead", gex_alerts._load_alerts_state(Path(td), DAY)["feedSirens"])

    def test_flow_dead_still_pages_on_the_native_book(self):
        with TemporaryDirectory() as td:
            rows = [{**_row(), "ts": (NOW.replace(hour=9, minute=30) + timedelta(minutes=i)).isoformat(), "gex_source": "native"}
                    for i in range(0, 80, 4)]
            rows.append({**_row(), "ts": NOW.isoformat(), "gex_source": "native"})
            _write_lens_rows(Path(td), rows)
            out, sent = self._run(td)
            self.assertEqual(out["feed_sirens"], 1)
            self.assertTrue(any("options flow dead" in s for s in sent))

    # --- the phone's status file ----------------------------------------------

    def test_the_status_file_says_down_only_once_the_wait_is_over(self):
        start = NOW.replace(hour=9, minute=31)
        with TemporaryDirectory() as td:
            _write_lens_rows(Path(td), _stand_in_rows(start, start + timedelta(minutes=8)))
            self._run(td, now=start + timedelta(minutes=8))
            self.assertEqual(_options_data(td), {
                "down": False, "since": None, "back_at": None, "blank_questions": [],
                "total_questions": 79, "updated": (start + timedelta(minutes=8)).isoformat()})
            _write_lens_rows(Path(td), _stand_in_rows(start, start + timedelta(minutes=16)))
            self._run(td, now=start + timedelta(minutes=16))
            doc = _options_data(td)
            self.assertTrue(doc["down"])
            self.assertEqual(doc["since"], start.isoformat())
            self.assertEqual(doc["blank_questions"], list(gex_alerts.PROVIDER_QUESTIONS))
            self.assertIsNone(doc["back_at"])

    def test_the_status_file_keeps_the_last_outage_once_it_is_back(self):
        start, healed = NOW.replace(hour=9, minute=31), NOW.replace(hour=14, minute=23)
        with TemporaryDirectory() as td:
            rows = _stand_in_rows(start, start + timedelta(minutes=16))
            _write_lens_rows(Path(td), rows)
            self._run(td, now=start + timedelta(minutes=16))
            _write_lens_rows(Path(td), rows + [{**_row(), "ts": healed.isoformat(), "gex_source": "native"}])
            self._run(td, now=healed)
            doc = _options_data(td)
            self.assertEqual((doc["down"], doc["since"], doc["back_at"], doc["blank_questions"]),
                             (False, start.isoformat(), healed.isoformat(), []))

    def test_no_status_file_without_a_fresh_row(self):
        with TemporaryDirectory() as td:
            _write_lens_rows(Path(td), [_row()])  # ts 10:00 vs NOW 13:00: the scanner is silent
            self._run(td)
            self.assertFalse(gex_alerts._options_data_path(Path(td)).exists())

    def test_a_fresh_row_clears_the_scanner_silent_siren(self):
        with TemporaryDirectory() as td:
            stale = _row()                         # ts 10:00 vs NOW 13:00
            _write_lens_rows(Path(td), [stale])
            self.assertEqual(self._run(td)[0]["feed_sirens"], 1)
            _write_lens_rows(Path(td), [self._fresh_row(), ])
            out, sent = self._run(td)
            self.assertEqual(out["feed_recoveries"], 1)
            self.assertTrue(any("scanner back" in s for s in sent))

    def test_an_undelivered_siren_is_not_marked_and_retries(self):
        """Delivery, not intent. A swallowed send that still marked the flag
        retired the one notification the outage was ever going to get."""
        def _dead(_msg):
            raise RuntimeError("ntfy unreachable")

        with TemporaryDirectory() as td:
            _write_lens_rows(Path(td), _stand_in_rows(NOW - timedelta(minutes=20), NOW))
            out1 = gex_alerts.run(NOW, state_dir=Path(td),
                                  redive_provider=lambda exp, ctx: None,
                                  channel=_dead)
            self.assertEqual(out1["feed_sirens"], 0)
            self.assertTrue(out1.get("undelivered"))
            out2, sent2 = self._run(td)            # channel back → it tries again
            self.assertEqual(out2["feed_sirens"], 1)
            self.assertTrue(any("Options data down" in s for s in sent2))

    def test_non_dict_state_file_resets_instead_of_crashing(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "market_expectation"
            p.mkdir(parents=True)
            (p / "alerts_state.json").write_text("null")   # valid JSON, not a dict
            r = self._fresh_row()
            r["native_coverage"] = {"no_zero_dte": True}
            _write_lens_rows(Path(td), [r])
            out, _ = self._run(td)                # must run, not AttributeError
            self.assertEqual(out["feed_sirens"], 1)

    def test_mangled_diary_bytes_cost_rows_not_the_run(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "reversion"
            d.mkdir(parents=True)
            import json as _json
            good = self._fresh_row()
            good["native_coverage"] = {"no_zero_dte": True}
            with open(d / f"{DAY}.jsonl", "wb") as f:
                f.write(b"\xff\xfe not json \n")        # invalid UTF-8 line
                f.write((_json.dumps(good) + "\n").encode())
            out, _ = self._run(td)                # must page on the good row
            self.assertEqual(out["feed_sirens"], 1)


def test_the_pages_question_counts_are_the_catalogs_own(monkeypatch):
    """gex_alerts imports nothing from spx_jev, so its copies of the provider's
    questions and the catalog's size are held to the code here."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "skills" / "spx-jev"))
    from spx_jev.mirai_prediction.code_features import GEX_BOOK_QUESTIONS, PROVIDER_QUESTIONS, load_catalog
    assert set(gex_alerts.PROVIDER_QUESTIONS) == PROVIDER_QUESTIONS and GEX_BOOK_QUESTIONS < PROVIDER_QUESTIONS
    assert len(gex_alerts.PROVIDER_QUESTIONS) == len(PROVIDER_QUESTIONS)
    assert gex_alerts.CODE_QUESTIONS_COUNT == len(load_catalog())
    assert PROVIDER_QUESTIONS <= {q["id"] for q in load_catalog()}


if __name__ == "__main__":
    unittest.main()
