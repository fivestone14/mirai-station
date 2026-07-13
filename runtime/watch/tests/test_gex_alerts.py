"""Tests for the gex-only ALERT BELL (intraday/gex_alerts.py) — the ported
wall-breach re-dive + paper-fire pushes + EOD mood scoring."""
from __future__ import annotations

import json
import unittest
from datetime import datetime
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
            # spot 7580 > call wall 7520 + buffer → concrete breach
            _write_lens_rows(Path(td), [_row(spot=7580.0)])
            out1, sent1, dives1 = self._run(td)
            self.assertEqual(out1["breaches"], 1)
            self.assertEqual(len(dives1), 1)             # re-dive fired
            self.assertTrue(any("wall" in s for s in sent1))
            out2, _, dives2 = self._run(td)              # same tick minutes → cooldown
            self.assertEqual(len(dives2), 0)

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
            self.assertGreater(p.hits + p.misses, 0)      # posterior updated
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

    def test_proxy_source_and_dead_book_each_page_separately(self):
        with TemporaryDirectory() as td:
            r = self._fresh_row()
            r["native_coverage"] = {"zero_dte_dead": True}
            r["gex_source"] = "spy_proxy\u00d710.0348"   # live vocabulary: per-tick rescale
            _write_lens_rows(Path(td), [r])
            out, sent = self._run(td)
            self.assertEqual(out["feed_sirens"], 2)
            self.assertTrue(any("half-dead" in s for s in sent))
            self.assertTrue(any("source:spy_proxy" in s for s in sent))
            # the rescale drifts every tick (11 distinct values on 07-10) — the
            # dedup key must be the FAMILY, or a proxy day is a pager storm
            r["gex_source"] = "spy_proxy\u00d710.0351"
            _write_lens_rows(Path(td), [r])
            out2, _ = self._run(td)
            self.assertEqual(out2["feed_sirens"], 0)

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



if __name__ == "__main__":
    unittest.main()
