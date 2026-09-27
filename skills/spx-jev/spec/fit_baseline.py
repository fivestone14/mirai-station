"""Fit and validate the price-only baseline (spx_jev/baseline.py), and freeze it in spec/baseline.json.

    python3 spec/fit_baseline.py                          # the station's state, read only
    python3 spec/fit_baseline.py --state-dir DIR --out spec/baseline.json

Every SPX session with a saved day of bars is replayed as the clock replays it and graded by the live
grader. The qualifying sessions (baseline.py) are the ones fitted and validated: each is scored by
tables fitted on all the others (leave one day out), and the day-mean gain of E_state over E_clock
decides the reference per horizon (E_state when the mean gain is above zero, else E_clock). The same
validation on every session, qualifying or not, and the flat share under the diary's anchor sigma are
recorded beside it as checks. The frozen tables are fitted on all qualifying sessions.
Read only: nothing under the state directory is written. The output is the only file touched.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spx_jev.baseline import BASELINE_FILE, MIN_ROWS, fit, leave_one_day_out, rule_hash, with_move  # noqa: E402
from spx_jev.clock import replayed_reads  # noqa: E402
from spx_jev.lane import LIVE  # noqa: E402
from spx_jev.row_adapter import labeller_row  # noqa: E402
from spx_jev.state_builder import (DEFAULT_STATE_DIR, MIN_BARS_FOR_A_SESSION, ROWS_SUBDIR, bar_days, load_bars,  # noqa: E402
                                   load_jsonl)

VERSION = 1
FLIPS = 10_000
SEED = 20260926


def session(state_dir: Path, day: str) -> tuple[list[dict], list[dict], list[dict]] | None:
    """``(bars, labeller rows, raw rows)`` for a day with a full session of bars, else None."""
    bars = load_bars(state_dir, day)
    if len(bars) < MIN_BARS_FOR_A_SESSION:
        return None
    raw = [r for r in load_jsonl(state_dir / ROWS_SUBDIR / f"{day}.jsonl") if r.get("ticker", "SPX") == "SPX"]
    rows = sorted((r for x in raw if (r := labeller_row(x))), key=lambda r: r["ts"])
    return bars, rows, raw


def qualifies(rows: list[dict], raw: list[dict]) -> bool:
    return len(rows) >= MIN_ROWS and any(isinstance(r.get("sigma_live"), (int, float)) for r in raw)


def gain_summary(lodo: dict[str, dict], better: str, worse: str) -> dict:
    """The day-mean gain of ``better`` over ``worse`` (log loss, positive when ``better`` is better):
    mean, standard error, days ahead, and a one-sided sign-flip p-value over the days."""
    gains = [d[worse]["log_loss"] - d[better]["log_loss"] for d in lodo.values()]
    if len(gains) < 2:
        return {"days": len(gains)}
    mean = statistics.fmean(gains)
    rng = random.Random(SEED)
    flips = sum(1 for _ in range(FLIPS) if statistics.fmean(g if rng.random() < 0.5 else -g for g in gains) >= mean)
    return {"days": len(gains), "mean_gain_nats": round(mean, 5), "se": round(statistics.stdev(gains) / len(gains) ** 0.5, 5),
            "days_ahead": sum(1 for g in gains if g > 0), "sign_flip_p": round((flips + 1) / (FLIPS + 1), 4)}


def means(lodo: dict[str, dict]) -> dict:
    return {f: {k: round(statistics.fmean(d[f][k] for d in lodo.values()), 5) for k in ("log_loss", "move", "direction")}
            for f in ("whole_day", "clock", "state")} if lodo else {}


def flat_share(replays: dict[str, list[dict]], h: str) -> float | None:
    bands = [r["bands"][h] for reads in replays.values() for r in reads if h in r["bands"]]
    return round(sum(1 for b in bands if b == "flat") / len(bands), 4) if bands else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fit, validate and freeze the price-only baseline (read only).")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out", default=str(BASELINE_FILE))
    args = ap.parse_args(argv)
    state_dir, horizons = Path(args.state_dir), list(LIVE.horizons)
    every, qualifying, anchored = {}, {}, {}
    for day in bar_days(state_dir):
        s = session(state_dir, day)
        if s is None or not s[1]:
            continue
        bars, rows, raw = s
        every[day] = with_move(replayed_reads(bars, rows, LIVE.horizons), float(bars[0]["open"]))
        if qualifies(rows, raw):
            qualifying[day] = every[day]
            anchor = {r["ts"]: r["sigma_anchor"] for r in raw if isinstance(r.get("sigma_anchor"), (int, float)) and r["sigma_anchor"] > 0}
            anchored[day] = replayed_reads(bars, [{**r, "sigma": anchor[r["ts"]]} for r in rows if r["ts"] in anchor], LIVE.horizons)
    lodo = leave_one_day_out(qualifying, horizons)
    lodo_every = leave_one_day_out(every, horizons)
    validation = {h: {"qualifying": {"mean_day_loss": means(lodo[h]), "state_over_clock": gain_summary(lodo[h], "state", "clock"),
                                     "clock_over_whole_day": gain_summary(lodo[h], "clock", "whole_day"),
                                     "reads": sum(d["reads"] for d in lodo[h].values())},
                      "every_session": {"mean_day_loss": means(lodo_every[h]), "state_over_clock": gain_summary(lodo_every[h], "state", "clock"),
                                        "reads": sum(d["reads"] for d in lodo_every[h].values())},
                      "flat_share": {"row_sigma": flat_share(qualifying, h), "sigma_anchor": flat_share(anchored, h)},
                      "per_day": {d: {f: round(v[f]["log_loss"], 5) for f in ("whole_day", "clock", "state")} for d, v in lodo[h].items()}}
                  for h in horizons}
    reference = {h: "state" if validation[h]["qualifying"]["state_over_clock"].get("mean_gain_nats", 0) > 0 else "clock" for h in horizons}
    doc = {"name": "spx-jev price-only baseline, frozen", "version": VERSION, "fitted_on": datetime.now().astimezone().date().isoformat(),
           "rule": "E_clock and E_state counted on the qualifying sessions' replayed reads, graded by the live grader on each row's sigma; "
                   "the reference per horizon is E_state when its leave-one-day-out day-mean gain over E_clock is above zero, else E_clock",
           "sessions": sorted(qualifying), "sessions_on_disk": sorted(every), **fit(qualifying, horizons),
           "reference": reference, "validation": validation}
    doc["rule_hash"] = rule_hash(doc)
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"{len(qualifying)} qualifying of {len(every)} SPX sessions -> {args.out}  reference {reference}")
    for h in horizons:
        v = validation[h]
        print(f"  {h}: mean day loss {v['qualifying']['mean_day_loss']}")
        print(f"       state over clock {v['qualifying']['state_over_clock']}; clock over whole day {v['qualifying']['clock_over_whole_day']}")
        print(f"       every session: state over clock {v['every_session']['state_over_clock']}; flat share {v['flat_share']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
