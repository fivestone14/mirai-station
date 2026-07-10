"""Mirai Watch CLI entrypoint (gex-only chassis, post 2026-07-03 restructure).

Usage:
    python -m watch.cli macro-brief    # morning Macro-Mood brief (launchd ~09:00 ET)
    python -m watch.cli gex-alerts     # per-tick alert bell: paper-fire pushes +
                                       #  wall-breach mood re-dive + EOD mood scoring
    python -m watch.cli doctor         # self-diagnosis

The bet-watching graph (`tick`), entry-signal learner (`learn`) and checkpoint
disposal (`gc`) were retired with the nine-voter system — the Fade Lens in
skills/mirai-left-eye is the brain now, and hunter.py runs the scan directly.
Backup of the old system: mirai-station.backup-pre-gexmodule-20260703.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import paths

ET = ZoneInfo("America/New_York")


def _run_macro_brief() -> int:
    """The morning Macro-Mood brief: sweep cross-sector news/sentiment and write the
    day's market_expectation (direction/magnitude + sectors + reasoning + embedding)
    that the gex tick reads as a light context tilt. BETA shadow layer."""
    from .intraday import macro_mood

    now = datetime.now(tz=ET)
    exp = macro_mood.run_brief(paths.STATE_DIR, now, reason="morning")
    if exp is None:
        print(f"[{now.strftime('%H:%M:%S')}] macro-brief: analyzer returned nothing "
              f"(no expectation written)", flush=True)
        return 1
    ov = exp.get("overall") or {}
    print(f"[{now.strftime('%H:%M:%S')}] macro-brief: dir={ov.get('direction')} "
          f"mag={ov.get('magnitude')} conf={ov.get('confidence')} "
          f"(embedded={exp.get('embedding') is not None})", flush=True)
    return 0


def _run_gex_alerts() -> int:
    """One alert pass after the scan: push fresh Fade-Lens paper fires, fire the
    wall-breach mood re-dive, score the mood at EOD. Best-effort by design."""
    from .intraday import gex_alerts

    out = gex_alerts.run()
    print(f"[{datetime.now(tz=ET).strftime('%H:%M:%S')}] gex-alerts: "
          f"pushed={out['pushed']} breaches={out['breaches']} "
          f"redives={out['redives']} eod_scored={out['eod_scored']}", flush=True)
    return 0


def _doctor() -> int:
    """Self-diagnosis: resolve all paths, check existence, check claude CLI, deps."""
    print("== Mirai Watch self-diagnostic (gex-only chassis) ==\n")
    print("Paths:")
    ok = True
    for row in paths.diagnostic():
        status = "OK " if row["exists"] else ("MISSING" if row["kind"] == "required" else "(absent — will create / user-supplied)")
        if not row["exists"] and row["kind"] == "required":
            ok = False
        print(f"  [{status:>10}] {row['name']:<20} {row['path']}")

    print("\nDependencies:")
    for mod in ("httpx", "schwab"):
        try:
            __import__(mod)
            print(f"  [OK        ] import {mod}")
        except ImportError as e:
            ok = False
            print(f"  [MISSING   ] import {mod}: {e}")

    print("\nClaude CLI:")
    claude_bin = shutil.which("claude")
    if claude_bin:
        print(f"  [OK        ] claude at {claude_bin}")
    else:
        ok = False
        print("  [MISSING   ] `claude` not found on PATH")

    print()
    print("== {} ==".format("ALL CHECKS PASSED" if ok else "ISSUES DETECTED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mirai-watch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("macro-brief", help="Run the morning Macro-Mood brief (writes the "
                                       "day's market_expectation; launchd ~09:00 ET).")
    sub.add_parser("gex-alerts", help="Per-tick alert bell: paper-fire pushes + "
                                      "wall-breach mood re-dive + EOD mood scoring.")
    sub.add_parser("doctor", help="Self-diagnose: paths, deps, claude CLI")

    args = parser.parse_args(argv)
    if args.cmd == "macro-brief":
        return _run_macro_brief()
    if args.cmd == "gex-alerts":
        return _run_gex_alerts()
    if args.cmd == "doctor":
        return _doctor()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
