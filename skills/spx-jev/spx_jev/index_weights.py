"""The index weights file, ``state/spx_leaders/weights.json``: each largest stock's share of the S&P 500, dated, for
the megacap labels (labels/leadership.py).

    python3 -m spx_jev.index_weights --show
    python3 -m spx_jev.index_weights --add 2026-10-06 --source "..." NVDA=0.078 MSFT=0.066 ...

The file holds every set of weights ever written, each under its ``as_of`` day:

    {"note": "...", "entries": [{"as_of": "2026-10-06", "source": "...", "weights": {"NVDA": 0.078, ...}}, ...]}

Point in time: a read on a day takes the newest entry dated on or before it (``pick``), never a later one, so a
day replayed next year reads the weights it had. A new set of weights is a new entry under its own day; an
entry on file is never rewritten, and ``--add`` refuses a day already on file. A file in the older shape, one
``{"as_of", "weights"}`` at the top, is read as one entry.

The weights are kept by hand (the first entry is approximate, from memory, not a holdings file, and says so with
``"approximate": true``, which the labels repeat in their sentences): ``validate``
holds each to a share between 0 and 1, the set to under the whole index, and the day to an ISO date. A share
class the market feed does not carry is summed into the one it does by the labels (leadership.SHARE_CLASSES);
Berkshire is named as the feed quotes it, "BRK/B".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .state_builder import DEFAULT_STATE_DIR

WEIGHTS_FILE = Path("spx_leaders") / "weights.json"


def entries(doc) -> list[dict]:
    """The file's entries, each ``{"as_of", "weights", ...}``; the older one-entry shape as a list of one."""
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("entries"), list):
        return [e for e in doc["entries"] if isinstance(e, dict)]
    return [doc] if "as_of" in doc else []


def pick(doc, day: str) -> dict | None:
    """The newest entry dated on or before ``day`` (YYYY-MM-DD), or None when every entry is later or undated."""
    dated = [e for e in entries(doc) if isinstance(e.get("as_of"), str) and e["as_of"] <= day]
    return max(dated, key=lambda e: e["as_of"]) if dated else None


def validate(doc) -> list[str]:
    """Everything wrong with the file, in words; an empty list when it is sound."""
    found = entries(doc)
    problems = [] if found else ["no entries"]
    days = []
    for e in found:
        as_of = e.get("as_of")
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError):
            problems.append(f"as_of {as_of!r} is not an ISO date")
            as_of = None
        if as_of in days:
            problems.append(f"as_of {as_of} is on file twice")
        days.append(as_of)
        weights = e.get("weights")
        if not isinstance(weights, dict) or not weights:
            problems.append(f"{as_of}: no weights")
            continue
        for symbol, w in weights.items():
            if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 < w < 1:
                problems.append(f"{as_of}: {symbol} = {w!r} is not a share between 0 and 1")
        shares = [w for w in weights.values() if isinstance(w, (int, float)) and not isinstance(w, bool)]
        if sum(shares) >= 1:
            problems.append(f"{as_of}: the weights sum to {sum(shares):.3f}, the whole index or more")
    return problems


def add(path: Path, as_of: str, weights: dict[str, float], source: str, note: str | None = None, approximate: bool = False) -> dict:
    """A new entry under ``as_of`` put after the ones on file, the file validated whole before it is written
    (ValueError names what is wrong), and written atomically; ``approximate`` marks a set not taken from a holdings
    file. Returns the document written."""
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if any(e.get("as_of") == as_of for e in entries(doc)):
        raise ValueError(f"{as_of} is already on file; a later set of weights goes under its own day")
    entry = {"as_of": as_of, "source": source, **({"approximate": True} if approximate else {}), "weights": weights}
    new = {"note": note or doc.get("note") or "", "entries": entries(doc) + [entry]}
    if problems := validate(new):
        raise ValueError("; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The index weights on file: show them, or add a set under a new as_of day.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--show", action="store_true", help="print the file and what validate finds")
    ap.add_argument("--add", metavar="AS_OF", help="add the SYMBOL=SHARE pairs under this day")
    ap.add_argument("--source", default="", help="with --add: where the weights come from")
    ap.add_argument("--note", help="with --add: the file's note, replacing the one on file")
    ap.add_argument("--approximate", action="store_true", help="with --add: the set is not from a holdings file; the labels say so")
    ap.add_argument("pairs", nargs="*", metavar="SYMBOL=SHARE")
    args = ap.parse_args(argv)
    path = Path(args.state_dir) / WEIGHTS_FILE
    if args.add:
        try:
            weights = {k: float(v) for k, v in (pair.split("=", 1) for pair in args.pairs)}
            doc = add(path, args.add, weights, args.source, args.note, args.approximate)
        except ValueError as e:
            print(f"spx-jev-index-weights :: not written: {e}", file=sys.stderr)
            return 1
        print(f"spx-jev-index-weights :: {len(doc['entries'])} entries, newest {args.add}, {len(weights)} names -> {path}")
        return 0
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    print(json.dumps(doc, indent=1))
    problems = validate(doc)
    print(f"spx-jev-index-weights :: {'sound' if not problems else '; '.join(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
