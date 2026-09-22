"""Does the number in a label help JEV or hurt it?

Three arms of the same request, differing only by deletion:

    full        "price rose 0.42 sigma, more than the 0.15 sigma move rule"
    no_figure   "price rose, more than the move rule"
    no_band     "price rose 0.42 sigma"

The ground truth is not a judgment of mine. For these questions the labeller
already decided which answer option the number fell into, and that decision is
recorded beside the label. So this scores JEV against your own code.

A question is only scored when exactly one of the labels it reads carries such a
decision. That rules out the forecasting questions, which have no right answer
until the 30 and 60 minutes have passed.

    python -m sndk_jev.ab_test --days 2026-09-15,2026-09-16 --every 20 --dry-run
    TYPESAFE_API_KEY=... python -m sndk_jev.ab_test --days ... --every 20 --out ab.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from .ask import build_requests, load_questions, paths_in, send_all, DEFAULT_QUESTIONS
from .state_builder import DEFAULT_STATE_DIR, build_ab, make_scene, variant_state

ARMS = ("full", "no_figure", "no_band")


def gradeable(doc: dict, verdicts: dict) -> dict[str, str]:
    """Question id to label path, for questions whose answer the code already knows.

    Only a true lookup qualifies: the question reads exactly one label apart from
    context, that label carries the code's verdict, and the verdict is one of the
    question's own options. A judgment question that reads several labels is never
    graded here, because no single label's verdict is its answer.
    """
    out: dict[str, str] = {}
    for group in doc["groups"]:
        for qid, q in group["questions"].items():
            paths = [p for p in paths_in(q) if not p.startswith("context.") and p != "context"]
            if len(paths) != 1 or paths[0] not in verdicts:
                continue
            crit = q.get("criteria")
            options = set(crit) if isinstance(crit, dict) else {"true", "false"} if q.get("type") == "noul" else set()
            if verdicts[paths[0]] in options:
                out[qid] = paths[0]
    return out


def pick(answer: dict) -> str | None:
    """What JEV chose, as an option name."""
    if not isinstance(answer, dict):
        return None
    if answer.get("type") == "choice":
        return answer.get("choice")
    if answer.get("type") == "noul":
        n = answer.get("noul")
        return None if not isinstance(n, (int, float)) else ("true" if n >= 0.5 else "false")
    if answer.get("type") == "score":
        # the level with the most probability, as its position in the ladder; the single
        # score is a weighted mean and can sit between two levels, so it is never used here
        probs = answer.get("probabilities")
        if isinstance(probs, dict) and probs:
            return str(max(probs, key=lambda k: probs[k]))
        if isinstance(probs, list) and probs:
            return str(max(range(len(probs)), key=lambda i: probs[i]))
    return None


def confidence(answer: dict) -> float | None:
    if not isinstance(answer, dict):
        return None
    if answer.get("type") == "choice":
        c = answer.get("confidence")
        return float(c) if isinstance(c, (int, float)) else None
    if answer.get("type") == "noul":
        n = answer.get("noul")
        return abs(float(n) - 0.5) * 2 if isinstance(n, (int, float)) else None
    return None


def moments(state_dir: str, days: list[str], every: int) -> list:
    out = []
    for day in days:
        t = datetime.combine(datetime.fromisoformat(day).date(), time(10, 0))
        end = datetime.combine(t.date(), time(15, 0))
        while t <= end:
            try:
                out.append((day, t.strftime("%H:%M"), make_scene(state_dir, day, at=t.time())))
            except ValueError:
                pass
            t += timedelta(minutes=every)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A/B whether the figure in a label helps JEV.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--days", required=True, help="comma-separated YYYY-MM-DD")
    ap.add_argument("--every", type=int, default=20, help="minutes between moments")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--out", help="write every answer here as JSON lines")
    ap.add_argument("--dry-run", action="store_true", help="build and count, send nothing")
    args = ap.parse_args(argv)

    doc = load_questions(args.questions)
    scenes = moments(args.state_dir, args.days.split(","), args.every)
    if not scenes:
        print("no usable moments in those days", file=sys.stderr)
        return 1

    out = open(args.out, "w", encoding="utf-8") if args.out else None
    hits: dict[str, list[int]] = defaultdict(list)
    confs: dict[str, list[float]] = defaultdict(list)
    per_q: dict[tuple[str, str], list[int]] = defaultdict(list)
    n_scored = n_moments = n_requests = 0
    identical: set[str] = set()
    truths: dict[str, list[str]] = defaultdict(list)

    try:
        for day, clock, scene in scenes:
            state, _omitted, variants, verdicts = build_ab(scene)
            graded = gradeable(doc, verdicts)
            if not graded:
                continue
            n_moments += 1
            for qid, path in graded.items():
                truths[qid].append(verdicts[path])
            for arm in ARMS:
                arm_state = variant_state(state, variants, arm)
                reqs, _skipped = build_requests(arm_state, doc)
                for req in reqs:
                    req["questions"] = {q: v for q, v in req["questions"].items() if q in graded}
                reqs = [r for r in reqs if r["questions"]]
                n_requests += len(reqs)
                if args.dry_run:
                    for req in reqs:
                        for qid in req["questions"]:
                            path = graded[qid]
                            forms = variants.get(path, {})
                            if forms and forms.get(arm) == forms.get("full") and arm != "full":
                                identical.add(f"{qid}/{arm}")
                            if out:
                                out.write(json.dumps({"day": day, "clock": clock, "arm": arm, "question": qid,
                                                      "label_path": path, "label": arm_state[path.split(".")[0]][path.split(".")[1]],
                                                      "code_says": verdicts[path]}, ensure_ascii=False) + "\n")
                    continue
                answers = send_all(reqs)
                for req in reqs:
                    ans = answers.get(req["id"]) or {}
                    if "answers" not in ans:
                        print(f"[{day} {clock} {arm} {req['id']}] {ans.get('error', 'no answer')}", file=sys.stderr)
                        continue
                    for qid, a in (ans.get("answers") or {}).items():
                        path = graded.get(qid)
                        if not path:
                            continue
                        chose, truth = pick(a), verdicts[path]
                        if chose is None:
                            continue
                        ok = int(chose == truth)
                        hits[arm].append(ok)
                        per_q[(qid, arm)].append(ok)
                        c = confidence(a)
                        if c is not None:
                            confs[arm].append(c)
                        n_scored += 1
                        if out:
                            out.write(json.dumps({"day": day, "clock": clock, "arm": arm, "question": qid,
                                                  "label_path": path, "label": arm_state[path.split(".")[0]][path.split(".")[1]],
                                                  "code_says": truth, "jev_says": chose, "correct": bool(ok),
                                                  "confidence": c, "model": ans.get("model")}, ensure_ascii=False) + "\n")
    finally:
        if out:
            out.close()

    baselines = {q: max(Counter(v).values()) / len(v) for q, v in truths.items() if v}

    print(f"\nmoments {n_moments}  requests {n_requests}  answers scored {n_scored}", file=sys.stderr)
    if args.dry_run:
        print("dry run: nothing was sent", file=sys.stderr)
        if identical:
            print(f"arms identical to full for: {', '.join(sorted(identical))}", file=sys.stderr)
        print(f"\n{'question':<26}{'answers seen':>14}{'always-guess':>14}  verdict spread", file=sys.stderr)
        for qid in sorted(truths):
            c = Counter(truths[qid])
            flag = "  TOO ONE-SIDED, drop it" if baselines[qid] >= 0.9 else ""
            print(f"{qid:<26}{len(truths[qid]):>14}{baselines[qid] * 100:>13.0f}%  {dict(c)}{flag}", file=sys.stderr)
        return 0
    if not n_scored:
        print("nothing scored", file=sys.stderr)
        return 1

    print(f"\n{'arm':<12}{'agrees with code':>18}{'n':>7}{'mean confidence':>18}", file=sys.stderr)
    for arm in ARMS:
        h = hits[arm]
        if not h:
            continue
        c = confs[arm]
        conf = f"{sum(c) / len(c):.2f}" if c else "n/a"
        print(f"{arm:<12}{sum(h) / len(h) * 100:>17.1f}%{len(h):>7}{conf:>18}", file=sys.stderr)

    print(f"\n{'question':<26}" + "".join(f"{a:>12}" for a in ARMS) + f"{'always-guess':>14}", file=sys.stderr)
    for qid in sorted({q for q, _ in per_q}):
        row = f"{qid:<26}"
        for arm in ARMS:
            v = per_q.get((qid, arm))
            row += f"{(sum(v) / len(v) * 100):>11.0f}%" if v else f"{'-':>12}"
        base = baselines.get(qid)
        row += f"{base * 100:>13.0f}%" if base is not None else f"{'-':>14}"
        print(row, file=sys.stderr)
    print("\nalways-guess is the score from always naming that question's most common answer.\n"
          "An arm that does not beat it has shown nothing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
