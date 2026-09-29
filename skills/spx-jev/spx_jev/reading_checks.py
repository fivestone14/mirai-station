"""Reading checks: JEV's archived picks against the answer a question's own label names, read only.

    python3 -m spx_jev.reading_checks --day 2026-09-29            # one day's archive, every lane
    python3 -m spx_jev.reading_checks --day 2026-09-28 --day 2026-09-29 --json

Many labels end a clause in the words of the option the rules pick ("...: a flat tilt for this minute", "...,
between the top and bottom fifths: ordinary for this half hour"), so the answer is in the sentence JEV read.
For every answered question in an archived read, the labels its instructions name are taken from the request
as it was sent and cut into clauses at their semicolons. An option is named by a clause that ends in its whole
description, or whose verdict (the words after the clause's last colon) is its description's lead, the words
before its colon. Words inside a clause never name one: "has not crossed back through where the reaction
started" is not the reversed option. When exactly one option is named that is the label's answer, and JEV's
pick is checked against it; a question whose labels name no option, or more than one, is not checked. A pick that differs
is a misreading worth rewording the question or the label for (the put tilt's steep-for-flat on 09-28 was
one), never a grade: nothing here feeds the weights, the sums or the phone.

Reads ``state/spx_jev/archive/{day}.jsonl`` and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .ask import get_path, paths_in, pick
from .state_builder import DEFAULT_STATE_DIR

ARCHIVE = Path("spx_jev") / "archive"
MIN_PHRASE_CHARS = 12          # a shorter lead of a description ("up", "flat") names nothing on its own


def names(description: str, clause: str) -> bool:
    """Whether one clause of a label (lower case) names the option with this description: the clause ends in the
    whole description, or its verdict is the description's lead."""
    whole = description.strip().lower()
    if clause == whole or clause.endswith((", " + whole, ": " + whole)):
        return True
    lead = whole.split(":", 1)[0].strip() if ":" in whole else ""
    return len(lead) >= MIN_PHRASE_CHARS and clause.rsplit(": ", 1)[-1] == lead


def options_of(question: dict) -> dict[str, str]:
    """A sent question's options by the name JEV picks: a choice's and a yes/no's by name, a score's by level."""
    crit = question.get("criteria") or {}
    return {str(i): d for i, d in enumerate(crit)} if isinstance(crit, list) else dict(crit)


def named_answer(question: dict, state: dict) -> str | None:
    """The one option whose words the question's labels carry, or None when they carry none or more than one."""
    clauses = [c.strip().lower() for p in paths_in(question) if isinstance(v := get_path(state, p), str) for c in v.split(";")]
    named = [name for name, description in options_of(question).items() if any(names(description, c) for c in clauses)]
    return named[0] if len(named) == 1 else None


def check_record(record: dict) -> list[dict]:
    """Every answered question in one archived read whose labels name an answer, with JEV's pick beside it."""
    out = []
    responses = record.get("responses") or {}
    for request in record.get("requests") or []:
        answers = (responses.get(request.get("id")) or {}).get("answers") or {}
        for qid, question in (request.get("questions") or {}).items():
            got = pick(answers.get(qid))
            named = named_answer(question, request.get("state") or {})
            if got is None or named is None:
                continue
            out.append({"read_id": record.get("read_id"), "lane": record.get("lane"), "question": qid, "pick": got,
                        "named": named, "agrees": got == named})
    return out


def check_day(state_dir: Path | str, day: str) -> list[dict]:
    """Every check in one day's archive; a line that is not JSON is skipped, as the store skips it."""
    path = Path(state_dir) / ARCHIVE / f"{day}.jsonl"
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("requests"):
            out.extend(check_record(record))
    return out


def summary(checks: list[dict]) -> dict[str, dict]:
    """By question: how many picks were checked, how many agreed, and each disagreement."""
    by: dict[str, dict] = defaultdict(lambda: {"checked": 0, "agreed": 0, "disagreed": []})
    for c in checks:
        s = by[c["question"]]
        s["checked"] += 1
        if c["agrees"]:
            s["agreed"] += 1
        else:
            s["disagreed"].append({"read_id": c["read_id"], "pick": c["pick"], "named": c["named"]})
    return dict(sorted(by.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check JEV's archived picks against the answer each question's labels name (read only).")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="mirai-station state directory")
    ap.add_argument("--day", action="append", required=True, help="a session date YYYY-MM-DD; repeat for more")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)
    checks = [c for day in args.day for c in check_day(args.state_dir, day)]
    by = summary(checks)
    if args.json:
        json.dump(by, sys.stdout, indent=1)
        print()
        return 0
    if not by:
        print(f"no answered question on {', '.join(args.day)} had labels naming one option")
        return 0
    for qid, s in by.items():
        print(f"{qid}: {s['agreed']} of {s['checked']} picks agree with the option its labels name")
        for d in s["disagreed"]:
            print(f"    {d['read_id']}: picked {d['pick']}, the labels name {d['named']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
