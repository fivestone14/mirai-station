"""Command line: build the JEV state for one SPX moment, or a day of them.

    python -m spx_jev.build                      # latest row of the latest day, to stdout
    python -m spx_jev.build --day 2026-09-25 --at 12:45
    python -m spx_jev.build --day 2026-09-25 --at 09:50 --lane tape
    python -m spx_jev.build --day 2026-09-25 --every 15 --out states.jsonl
    python -m spx_jev.build --send               # also post each request to JEV

Reads the station's stored rows, bars and market context only. ``--send`` needs TYPESAFE_API_KEY
in the environment and never prints it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time as _clock
from datetime import datetime, time, timedelta

from .ask import build_requests, load_questions, send_all, summarize
from .labels.registry import build_labels
from .lane import LANES, Lane
from .schedule import not_due
from .state_builder import DEFAULT_STATE_DIR, Scene, make_scene


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def package(scene: Scene, doc: dict, lane: Lane) -> dict:
    """One moment's labels and the requests the lane would send: the questions its schedule asks at this
    read and whose gates are awake. Nothing is held here: a held answer needs the service's records."""
    labels = build_labels(scene)
    requests, skipped = build_requests(labels.state, doc, skip=not_due(doc, lane, scene.now), gates=labels.gates)
    return {
        "row_ts": scene.row["ts"],
        "sigma": scene.sigma,
        "bars_used": len(scene.bars),
        "prior_sessions": len(scene.prior_bars),
        **({"ruler": scene.unit} if scene.bar_clock else {}),
        "state": labels.state,
        "omitted": labels.omitted,
        "requests": requests,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build JEV state and requests from the SPX diary rows and bars.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="mirai-station state directory")
    ap.add_argument("--day", help="session date YYYY-MM-DD, default the latest day with rows")
    ap.add_argument("--at", type=_time, help="ET clock HH:MM, use the last row at or before it")
    ap.add_argument("--every", type=int, help="replay the day, one state every N minutes from 10:00 to 15:00, as JSON lines")
    ap.add_argument("--lane", choices=sorted(LANES), default="live", help="live (the default) or tape: the bar clock, the tape unit, the lane's questions")
    ap.add_argument("--questions", help="questions file, default the lane's")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--send", action="store_true", help="post each request to JEV and print the answers")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    args = ap.parse_args(argv)

    lane = LANES[args.lane]
    doc = load_questions(args.questions or lane.questions, lane.key)
    horizon = f"the next {lane.horizons[lane.primary][0]} minutes"

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        if args.every:
            if not args.day:
                ap.error("--every needs --day")
            t = datetime.combine(datetime.fromisoformat(args.day).date(), time(10, 0))
            end = datetime.combine(t.date(), time(15, 0))
            n = 0
            while t <= end:
                try:
                    scene = make_scene(args.state_dir, args.day, at=t.time(), bar_clock=lane.bar_clock, horizon=horizon)
                    pkg = package(scene, doc, lane)
                    pkg["asked_at"] = t.strftime("%H:%M")
                    out.write(json.dumps(pkg, ensure_ascii=False) + "\n")
                    n += 1
                except ValueError as e:
                    out.write(json.dumps({"asked_at": t.strftime("%H:%M"), "error": str(e)}) + "\n")
                t += timedelta(minutes=args.every)
            print(f"wrote {n} states", file=sys.stderr)
            return 0

        scene = make_scene(args.state_dir, args.day, at=args.at, bar_clock=lane.bar_clock, horizon=horizon)
        pkg = package(scene, doc, lane)
        if args.send:
            t0 = _clock.monotonic()
            pkg["answers"] = send_all(pkg["requests"])
            pkg["send_seconds"] = round(_clock.monotonic() - t0, 3)
            for rid, ans in pkg["answers"].items():
                if "answers" not in ans:
                    print(f"[{rid}] {ans.get('error', 'no answer')}", file=sys.stderr)
                    continue
                print(f"[{rid}] model {ans.get('model')}", file=sys.stderr)
                for line in summarize(ans):
                    print("   " + line, file=sys.stderr)
            print(f"{len(pkg['requests'])} requests sent together in {pkg['send_seconds']} s", file=sys.stderr)
        out.write(json.dumps(pkg, ensure_ascii=False, indent=None if args.compact else 2) + "\n")
        n_q = sum(len(r["questions"]) for r in pkg["requests"])
        print(f"row {pkg['row_ts']}  labels {sum(len(v) for v in pkg['state'].values())}  omitted {len(pkg['omitted'])}  "
              f"requests {len(pkg['requests'])}  questions {n_q}", file=sys.stderr)
        return 0
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    raise SystemExit(main())
