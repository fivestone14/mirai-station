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
            self.assertEqual(len(sent1), 1)
            self.assertIn("paper", sent1[0])
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


if __name__ == "__main__":
    unittest.main()
