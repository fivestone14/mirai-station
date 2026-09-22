"""Command line: build the JEV state for one SNDK PRO moment, or a day of them.

    python -m sndk_jev.build                      # latest row of the latest day, to stdout
    python -m sndk_jev.build --day 2026-09-18 --at 12:45
    python -m sndk_jev.build --day 2026-09-18 --every 15 --out states.jsonl
    python -m sndk_jev.build --news item.json     # add a news item to the state
    python -m sndk_jev.build --send               # also post each request to JEV

Reads SNDK PRO's stored rows and bars only. ``--send`` needs TYPESAFE_API_KEY in
the environment and never prints it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time as _clock
from datetime import datetime, time, timedelta

from .ask import DEFAULT_QUESTIONS, build_requests, load_questions, send_all, summarize
from .state_builder import DEFAULT_STATE_DIR, Scene, build_state, make_scene


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def package(scene: Scene, doc: dict) -> dict:
    state, omitted = build_state(scene)
    requests, skipped = build_requests(state, doc)
    return {
        "row_ts": scene.row["ts"],
        "book_asof": (scene.row.get("meta") or {}).get("book_asof"),
        "sigma": scene.sigma,
        "bars_used": len(scene.bars),
        "prior_sessions": len(scene.prior_bars),
        "state": state,
        "omitted": omitted,
        "requests": requests,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build JEV state and requests from SNDK PRO rows and bars.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="mirai-station state directory")
    ap.add_argument("--day", help="session date YYYY-MM-DD, default the latest day with rows")
    ap.add_argument("--at", type=_time, help="ET clock HH:MM, use the last row at or before it")
    ap.add_argument("--every", type=int, help="replay the day, one state every N minutes from 10:00 to 15:00, as JSON lines")
    ap.add_argument("--news", help="path to a news item JSON to add to the state")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="questions file, default questions/sndk_pro.json")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--send", action="store_true", help="post each request to JEV and print the answers")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    args = ap.parse_args(argv)

    doc = load_questions(args.questions)
    news = None
    if args.news:
        with open(args.news, encoding="utf-8") as f:
            news = json.load(f)

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
                    scene = make_scene(args.state_dir, args.day, at=t.time(), news=news)
                    pkg = package(scene, doc)
                    pkg["asked_at"] = t.strftime("%H:%M")
                    out.write(json.dumps(pkg, ensure_ascii=False) + "\n")
                    n += 1
                except ValueError as e:
                    out.write(json.dumps({"asked_at": t.strftime("%H:%M"), "error": str(e)}) + "\n")
                t += timedelta(minutes=args.every)
            print(f"wrote {n} states", file=sys.stderr)
            return 0

        scene = make_scene(args.state_dir, args.day, at=args.at, news=news)
        pkg = package(scene, doc)
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
