"""The day's fits: the three learners fitted each night on the days before, saved under catalogs/voice_fits/ with the
answer matrix they were fitted on.

fit_voices_for_day(state_dir, root, lane, sum_id, day) builds the answer matrix for ``day`` (reads from days strictly
before it), fits additive_scorer, matcher and jev_corrected, and writes {day}_{sum_id}.json with a leak check: no row used
may be from ``day`` or later. The matrix travels in the same file, so the live hook needs no store scan: it loads the
fits and the matrix together, adds today's row, and the matcher compares against the matrix's rows.
load_voice_fits returns the fits for a day, or the newest earlier day's when that day has none yet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .additive_scorer import AdditiveScorerFit, fit_additive_scorer
from .answer_matrix import AnswerMatrix, build_answer_matrix
from .jev_corrected import JevCorrectedFit, fit_jev_corrected
from .matcher import MatcherFit, fit_matcher
from .paths import CATALOGS, VOICE_FITS, now_utc_iso, voice_fits_file, write_json_atomically

FIT_VERSION = 2


class LeakError(RuntimeError):
    """A fit for a day saw a row from that day or later."""


@dataclass
class DayFits:
    """What one fit file holds: the three learners, the matrix they were fitted on, and the day they are for."""
    fits: dict                      # {"additive_scorer": ..., "matcher": ..., "jev_corrected": ...}
    matrix: AnswerMatrix
    fit_day: str


def check_no_leak(matrix: AnswerMatrix, day: str) -> None:
    newest = matrix.max_day()
    if newest is not None and newest >= day:
        raise LeakError(f"a fit for {day} would use rows up to {newest}")


def fit_voices_for_day(state_dir: Path | str, root: Path, lane: str, sum_id: str, day: str) -> tuple[DayFits, Path]:
    """Fit every learner for ``day`` on reads from days before it; save and return (DayFits, path)."""
    matrix = build_answer_matrix(state_dir, lane, sum_id, before_day=day)
    check_no_leak(matrix, day)
    fits = {"additive_scorer": fit_additive_scorer(matrix), "matcher": fit_matcher(matrix), "jev_corrected": fit_jev_corrected(matrix)}
    doc = {"version": FIT_VERSION, "lane": lane, "sum_id": sum_id, "fit_day": day, "fitted_at": now_utc_iso(),
           "rows_in_matrix": len(matrix.rows), "trainable_rows": len(matrix.trainable_rows()), "max_day_used": matrix.max_day(),
           "columns_by_layer": {str(layer): sum(1 for m in matrix.columns.values() if m["layer"] == layer) for layer in (1, 2, 3)},
           "additive_scorer": fits["additive_scorer"].to_json(), "matcher": fits["matcher"].to_json(),
           "jev_corrected": fits["jev_corrected"].to_json(), "answer_matrix": matrix.to_json()}
    path = voice_fits_file(root, day, sum_id)
    write_json_atomically(path, doc)
    return DayFits(fits=fits, matrix=matrix, fit_day=day), path


def day_fits_from_document(doc: dict) -> DayFits:
    fits = {"additive_scorer": AdditiveScorerFit.from_json(doc["additive_scorer"]), "matcher": MatcherFit.from_json(doc["matcher"]),
            "jev_corrected": JevCorrectedFit.from_json(doc["jev_corrected"])}
    return DayFits(fits=fits, matrix=AnswerMatrix.from_json(doc["answer_matrix"]), fit_day=doc["fit_day"])


def load_voice_fits(root: Path, sum_id: str, day: str, allow_earlier: bool = True) -> DayFits | None:
    """The DayFits for ``day``; with ``allow_earlier`` the newest earlier day's when the day has none.
    A fit from a LATER day is never returned (it would have seen the read's own day)."""
    folder = root / CATALOGS / VOICE_FITS
    if not folder.exists():
        return None
    candidates = sorted(p for p in folder.glob(f"*_{sum_id}.json") if p.name[:10] <= day)
    if not candidates:
        return None
    chosen = candidates[-1]
    if chosen.name[:10] != day and not allow_earlier:
        return None
    doc = json.loads(chosen.read_text(encoding="utf-8"))
    if doc.get("max_day_used") and doc["max_day_used"] >= day:
        raise LeakError(f"fit {chosen.name} used rows up to {doc['max_day_used']}, not before {day}")
    if doc.get("version", 1) < FIT_VERSION:
        return None                              # an older file without the matrix; the caller fits afresh
    return day_fits_from_document(doc)


def next_market_day(day: str) -> str:
    """The next weekday after ``day`` (holidays are skipped by the jobs' own market-day gates)."""
    d = date.fromisoformat(day) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()
