"""Tests for the Morning Macro-Mood layer (intraday/macro_mood.py) — pure
logic, persistence, and expectation building. (The tick-graph integration
tests were retired with the graph; breach/EOD wiring is covered by
test_gex_alerts.py.)"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from watch.intraday import bayes, macro_mood

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 6, 11, 13, 0, tzinfo=ET)


def _exp(direction=0.5, confidence=0.6, embedding=None):
    return {
        "date": "2026-06-11", "ts": NOW.isoformat(), "reason": "morning",
        "overall": {"direction": direction, "magnitude": 0.8, "confidence": confidence},
        "sectors": {"tech": {"direction": 0.4, "magnitude": 0.7},
                    "semis": {"direction": 0.8, "magnitude": 0.9}},
        "reasoning": "risk-on; tech leads on soft CPI", "embedding": embedding or [1.0, 0.0, 0.0],
    }


class TestPureLogic(unittest.TestCase):
    def test_cosine(self):
        self.assertAlmostEqual(macro_mood.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(macro_mood.cosine([1, 0], [0, 1]), 0.0)
        self.assertIsNone(macro_mood.cosine(None, [1, 0]))
        self.assertIsNone(macro_mood.cosine([0, 0], [1, 0]))   # zero norm
        self.assertIsNone(macro_mood.cosine([1, 0], [1, 0, 0]))  # length mismatch

    def test_reliability_weight_starts_neutral(self):
        self.assertEqual(macro_mood.reliability_weight(None), 0.0)
        self.assertEqual(macro_mood.reliability_weight(bayes.Posterior()), 0.0)  # no evidence
        strong = macro_mood.reliability_weight(bayes.Posterior(hits=30, misses=2))
        self.assertGreater(strong, 0.4)
        wrong = macro_mood.reliability_weight(bayes.Posterior(hits=2, misses=30))
        self.assertEqual(wrong, 0.0)  # proven wrong -> no voice, never negative


    def test_wall_breach_beyond_buffer_only(self):
        # buffer = 0.10 * implied_move(40) = 4.0
        self.assertIsNone(macro_mood.wall_breach(5002, 5000, 4950, 40, 0.10))  # inside buffer
        call = macro_mood.wall_breach(5030, 5000, 4950, 40, 0.10)
        self.assertEqual((call["wall"], call["direction"]), ("call", 1))
        put = macro_mood.wall_breach(4940, 5000, 4950, 40, 0.10)
        self.assertEqual((put["wall"], put["direction"]), ("put", -1))
        self.assertIsNone(macro_mood.wall_breach(None, 5000, 4950, 40, 0.10))

    def test_score_direction(self):
        self.assertTrue(macro_mood.score_direction(0.5, 12.0))
        self.assertFalse(macro_mood.score_direction(0.5, -12.0))
        self.assertIsNone(macro_mood.score_direction(0.5, 0.0))   # flat day
        self.assertIsNone(macro_mood.score_direction(0.0, 12.0))  # flat call
        self.assertIsNone(macro_mood.score_direction(None, 1.0))


class TestPersistence(unittest.TestCase):
    def test_write_read_latest_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td)
            macro_mood.write_expectation(sd, _exp())
            got = macro_mood.read_expectation(sd, "2026-06-11")
            self.assertEqual(got["overall"]["direction"], 0.5)
            self.assertEqual(macro_mood.read_latest(sd)["date"], "2026-06-11")
            self.assertIsNone(macro_mood.read_expectation(sd, "2026-01-01"))

    def test_reliability_roundtrip_and_score_eod_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td)
            macro_mood.write_expectation(sd, _exp(direction=0.5))
            # realized +30 vs predicted +0.5 -> a hit
            won = macro_mood.score_eod(sd, _exp(direction=0.5), 30.0, "2026-06-11")
            self.assertTrue(won)
            p = macro_mood.read_reliability(sd)
            self.assertEqual(p.hits, 1.0)
            # second call same day is a no-op (idempotent)
            self.assertIsNone(macro_mood.score_eod(sd, _exp(direction=0.5), 30.0, "2026-06-11"))
            self.assertEqual(macro_mood.read_reliability(sd).hits, 1.0)

    def test_score_eod_flat_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td)
            self.assertIsNone(macro_mood.score_eod(sd, _exp(), 0.0, "2026-06-11"))
            self.assertEqual(macro_mood.read_reliability(sd).hits, 0.0)


class TestBuildExpectation(unittest.TestCase):
    def test_build_with_stubs_and_drift(self):
        prior = _exp(embedding=[1.0, 0.0, 0.0])
        analyze = lambda: {"overall": {"direction": -0.3, "magnitude": 0.5, "confidence": 0.4},
                           "sectors": {}, "reasoning": "risk-off now, yields spike"}
        embed = lambda text: [0.0, 1.0, 0.0]
        exp = macro_mood.build_expectation(NOW, analyze, embed, prior=prior, reason="redive:SPX:call")
        self.assertEqual(exp["overall"]["direction"], -0.3)
        self.assertEqual(exp["embedding"], [0.0, 1.0, 0.0])
        self.assertAlmostEqual(exp["drift_cosine"], 0.0)   # orthogonal -> story changed
        self.assertEqual(exp["reason"], "redive:SPX:call")

    def test_build_returns_none_on_analyzer_failure(self):
        self.assertIsNone(macro_mood.build_expectation(NOW, lambda: None, None))
        self.assertIsNone(macro_mood.build_expectation(NOW, lambda: (_ for _ in ()).throw(RuntimeError()), None))

