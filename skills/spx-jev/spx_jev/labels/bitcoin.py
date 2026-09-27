"""The bitcoin family: micro bitcoin futures (/MBT) against the index. Before the open, bitcoin's night
against the S&P futures' night (overnight.btc_vs_futures) and, on the first session after a weekend or a
holiday, its weekend leg against what it did once the S&P futures reopened (weekend.btc_path); in the
session, its half hour against the index's (xasset.btc_gap_30min), three such half hours in a row
(xasset.btc_gap_streak), how tightly the two trade together today (xasset.btc_link) and its last five
sessions against the index's (xasset.btc_five_day).

Every "which way" is the direction that has gone with stocks rising or falling over the last nights or
sessions, and every size a rank against them, so the learning loop sets the sign. Schwab's bitcoin
history is one stitched front-month series: a move across a roll (rolls.py, the Thursday before the
last-Friday expiry) is never measured. Each label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("overnight.btc_vs_futures", "weekend.btc_path", "xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link",
          "xasset.btc_five_day")
GATES = ("btc_overnight_vs_futures", "btc_weekend_path", "btc_gap_30m", "btc_gap_streak", "btc_link_today", "btc_five_day_lead")
DARK: dict[str, str] = {}
# Written only before the open; the session's four are the ones a premarket read leaves out.
PREMARKET = ("overnight.btc_vs_futures", "weekend.btc_path")


def build_bitcoin_labels(scene: Scene) -> LabelSet:
    return LabelSet()
