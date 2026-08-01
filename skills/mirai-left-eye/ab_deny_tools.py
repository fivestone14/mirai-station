"""A/B BENCH — does denying tools buy a shallower think, or just a cheaper one?

The tower's `claude -p` call ships the CLI's whole tool catalogue on every scan
and then forbids the model from touching any of it. Those schemas are the single
largest line in the request: 2026-07-30 the cached read block stepped 29.5k ->
35.9k tokens per call, and the tower makes 35-80 calls a morning. DENY_TOOLS
strips them. The saving is not in question.

What IS in question is what leaves with them. The 2026-07-29 test read tools-off
as a shallower think — wall 44.0s -> 15.7s, stated conviction 0.38 -> 0.31 — and
the flag was left off. That test was six samples on ONE marginal scene, and its
loudest number (fire rate 5/6 vs 3/6) evaporated when the SAME arm was re-run.
The note left behind said the honest test is wall and conviction over MANY
scenes. This is that test, plus the thing the first one could not do:

  SCORE THE VERDICTS AGAINST WHAT PRICE ACTUALLY DID.

Wall time and conviction measure how hard the model worked and how sure it says
it is. Neither says whether it was RIGHT. The diary carries a spot print every
minute, so a verdict made at 10:15 with horizon 60 can be marked against the
realized move at 11:15 — ground truth that owes nothing to the tower's own
grader. If tools-off thinks less AND forecasts worse, the flag stays off and we
pay the 35.9k. If it thinks less and forecasts the same, we were buying wall
time, not accuracy, and the tower has been overpaying by ~60% a call.

DESIGN — paired, stratified, interleaved:
  * PAIRED: both arms judge the SAME scene, so scene difficulty cancels.
  * STRATIFIED: scenes are drawn across both gamma regimes and both gate states,
    because a long-gamma pin and a short-gamma break are not the same question
    and 07-29 sampled only one of them.
  * INTERLEAVED: arms alternate in time, so a slow API stretch cannot land on
    one arm and masquerade as a thinking effect. This is what the wall-time
    result of 07-29 could not rule out.
  * REPEATED: k samples per scene per arm, because a single sample on a binary
    outcome is noise — the lesson that killed the 5/6-vs-3/6 read.

READ-ONLY on the live path. This module imports watchtower and calls the model;
it never writes to the diary, never touches state the scanner reads, and does
not flip DENY_TOOLS. It only prints a table and drops its raw samples in
state/ab_deny_tools/.

    python3 ab_deny_tools.py                 # full run (~144 calls)
    python3 ab_deny_tools.py --dry-run       # pick scenes, call nothing
    python3 ab_deny_tools.py --days 2 --scenes 2 --samples 2   # cheap pilot
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchtower as wt  # noqa: E402  (path juggling first, on purpose)

OUT_DIR = wt.STATE_DIR / "ab_deny_tools"
DIARY_DIR = wt.STATE_DIR / "reversion"

DAYS = 6                # trading days to draw scenes from (most recent first)
SCENES_PER_DAY = 4      # stratified across gamma sign x gate state
SAMPLES = 3             # samples per scene per arm
CONCURRENCY = 4         # parallel calls; the CLI is the bottleneck, not us
CALL_TIMEOUT_S = 150.0  # generous — a slow arm must not be scored as a timeout


# ---------------------------------------------------------------- scenes -----
def _diary_days(n: int) -> list[str]:
    """The n most recent recorded sessions that actually hold rows."""
    days = sorted(p.stem for p in DIARY_DIR.glob("2026-*.jsonl"))
    return days[-n:]


def _rows(day: str) -> list[dict]:
    try:
        return [json.loads(ln) for ln in (DIARY_DIR / f"{day}.jsonl").read_text().splitlines() if ln.strip()]
    except OSError:
        return []


def _stratum(row: dict) -> str:
    """Which corner of the map this scene sits in. The two axes that change the
    QUESTION being asked: gamma sign (does the tape damp or amplify?) and whether
    the gates fired (is the tower agreeing or dissenting?)."""
    gs = ((row.get("gex_views") or {}).get("gamma_sign_at_spot")
          or row.get("gamma_sign") or "unknown")
    sign = "long" if str(gs).lower().startswith(("long", "pos", "+")) else \
           "short" if str(gs).lower().startswith(("short", "neg", "-")) else "unknown"
    fired = "fired" if (row.get("reversion_extreme") or {}).get("fired") else "quiet"
    return f"{sign}/{fired}"


def pick_scenes(days: int, per_day: int) -> list[dict]:
    """Stratified draw. Within a day, take only scans the live prefilter would
    have judged, thin them to one per 15 minutes (an armed scene stays armed for
    hours — adjacent rows are the same question asked twice), then spread the
    picks across as many strata as the day offers before doubling up in any one."""
    picked = []
    for day in _diary_days(days):
        rows = _rows(day)
        cand, last_ts = [], None
        for r in rows:
            ok, why = wt.interesting(r.get("reversion_extreme") or {})
            if not ok or not r.get("spot") or not r.get("sigma"):
                continue
            ts = datetime.fromisoformat(r["ts"])
            if last_ts and (ts - last_ts) < timedelta(minutes=15):
                continue
            last_ts = ts
            cand.append({"day": day, "ts": r["ts"], "why": why,
                         "stratum": _stratum(r), "row": r})
        by_stratum: dict[str, list[dict]] = {}
        for c in cand:
            by_stratum.setdefault(c["stratum"], []).append(c)
        # Round-robin the strata so one regime cannot own the day's picks, and
        # deal the FIRED strata first. Gates firing is rare (roughly one scene a
        # day) and it is the scene the tower exists for — sorting by size alone
        # buries it under the quiet majority and the run never sees a live fight.
        order = sorted(by_stratum,
                       key=lambda k: (not k.endswith("/fired"), -len(by_stratum[k])))
        day_picks, i = [], 0
        while len(day_picks) < per_day and any(by_stratum.values()):
            s = order[i % len(order)]
            if by_stratum[s]:
                # spread within the stratum: take from opposite ends of the session
                day_picks.append(by_stratum[s].pop(len(by_stratum[s]) // 2))
            i += 1
            if i > 200:
                break
        picked.extend(day_picks)
    return picked


# ----------------------------------------------------------- ground truth ----
def realized(scene: dict, horizon_min: int) -> dict | None:
    """What price ACTUALLY did over the verdict's own horizon, in sigma units of
    the scene. Returns the signed move, the largest excursion either way, and
    how many minutes of tape were really available (a 15:50 verdict with a 60-min
    horizon only gets 10 minutes — scored on what exists, flagged as truncated)."""
    rows = _rows(scene["day"])
    t0 = datetime.fromisoformat(scene["ts"])
    spot0, sigma = scene["row"].get("spot"), scene["row"].get("sigma")
    if not spot0 or not sigma:
        return None
    end = t0 + timedelta(minutes=horizon_min or 60)
    path = []
    for r in rows:
        try:
            t = datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError):
            continue
        if t0 < t <= end and r.get("spot"):
            path.append((t, r["spot"]))
    if not path:
        return None
    moves = [(p - spot0) / sigma for _, p in path]
    return {"move_sigma": round(moves[-1], 3),
            "max_up_sigma": round(max(moves + [0.0]), 3),
            "max_dn_sigma": round(min(moves + [0.0]), 3),
            "excursion_sigma": round(max(abs(m) for m in moves), 3),
            "minutes": int((path[-1][0] - t0).total_seconds() // 60),
            "truncated": (path[-1][0] < end - timedelta(minutes=5))}


def score(v: dict, real: dict | None, expand_bar: float = 1.0) -> dict:
    """Mark one verdict against the tape. Directional calls are scored only when
    the tower actually fired — a no-fire is not a wrong direction, it is no bet,
    and counting it either way is how a coin-flip starts looking like an edge.
    Stance needs a yardstick the tape can actually reach. A fixed 1-sigma bar
    looks principled and measures NOTHING: across 144 scored samples not one
    scene moved a full sigma inside its horizon (median excursion 0.21σ), so
    every scene graded as 'settle was right' and stance_hit collapsed into "how
    often did this arm say settle". `expand_bar` is therefore passed in by the
    caller as the MEDIAN realized excursion of the run — a median split, so
    fight and settle are each right half the time by chance and the number
    means something."""
    if real is None:
        return {}
    out = {"realized_move_sigma": real["move_sigma"],
           "realized_excursion_sigma": real["excursion_sigma"]}
    if v.get("fired") and v.get("direction") in ("call", "put"):
        want = 1 if v["direction"] == "call" else -1
        out["dir_hit"] = (want * real["move_sigma"]) > 0
        # did the move it promised actually show up, in the direction promised?
        mag = float(v.get("magnitude_sigma") or 0)
        reach = real["max_up_sigma"] if want > 0 else abs(real["max_dn_sigma"])
        out["mag_hit"] = bool(mag) and reach >= mag * 0.5
    st = v.get("stance")
    if st in ("fight", "settle"):
        expanded = real["excursion_sigma"] >= expand_bar
        out["stance_hit"] = (st == "fight") == expanded
    return out


# ------------------------------------------------------------------ call -----
def ask(body: str, doctrine: str, model: str, deny: bool) -> dict:
    """One measured call. Mirrors watchtower._ask_claude's command EXACTLY — same
    flags, same order, same append-not-replace system block — but keeps the JSON
    envelope instead of throwing it away, because the envelope is where the token
    counts live and token counts are half of what this bench exists to compare."""
    cmd = ["claude", "-p", body, "--model", model, "--output-format", "json",
           "--append-system-prompt", doctrine]
    if deny:
        cmd.extend(["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--disallowedTools", *wt._NO_TOOLS])
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "wall_s": round(time.time() - t0, 1)}
    wall = round(time.time() - t0, 1)
    if r.returncode != 0:
        return {"error": f"rc={r.returncode}: {(r.stderr or '')[-200:]}", "wall_s": wall}
    env = wt._extract_json(r.stdout) or {}
    text = env.get("result") if isinstance(env, dict) else r.stdout
    v = wt.validate_verdict(wt._extract_json(text if isinstance(text, str) else r.stdout))
    u = (env.get("usage") or {}) if isinstance(env, dict) else {}
    return {"wall_s": wall, "verdict": v,
            "in": u.get("input_tokens", 0),
            "cache_write": u.get("cache_creation_input_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0),
            "out": u.get("output_tokens", 0),
            "cost_usd": env.get("total_cost_usd") if isinstance(env, dict) else None,
            "error": None if v else "bad verdict"}


def run(scenes: list[dict], samples: int, model: str) -> list[dict]:
    """Every (scene, arm, sample) as one flat job list, ordered so the two arms
    alternate. Interleaving is the whole point: if the API slows down for ten
    minutes, both arms eat it, and the wall-time comparison survives."""
    jobs = []
    for s in scenes:
        doctrine, body = wt._prompt_parts(wt.build_payload(s["row"]))
        for k in range(samples):
            for deny in (False, True):
                jobs.append({"scene": s, "sample": k, "deny": deny,
                             "doctrine": doctrine, "body": body})

    done, total = [], len(jobs)
    print(f"{total} calls: {len(scenes)} scenes x {samples} samples x 2 arms\n")

    def work(j):
        res = ask(j["body"], j["doctrine"], model, j["deny"])
        v = res.get("verdict") or {}
        rec = {"day": j["scene"]["day"], "ts": j["scene"]["ts"],
               "stratum": j["scene"]["stratum"], "why": j["scene"]["why"],
               "arm": "tools_off" if j["deny"] else "tools_on",
               "sample": j["sample"], **{k: x for k, x in res.items() if k != "verdict"},
               "fired": v.get("fired"), "direction": v.get("direction"),
               "stance": v.get("stance"),
               "conviction": v.get("conviction_stated", v.get("conviction")),
               "magnitude_sigma": v.get("magnitude_sigma"),
               "horizon_min": v.get("horizon_min")}
        if v:
            rec["_real"] = realized(j["scene"], int(v.get("horizon_min") or 60))
            rec["_v"] = v
        return rec

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for i, rec in enumerate(pool.map(work, jobs), 1):
            done.append(rec)
            if i % 10 == 0 or i == total:
                print(f"  {i}/{total}", flush=True)
    return _rescore(done)


def _rescore(recs: list[dict]) -> list[dict]:
    """Second pass: grade every sample against the run's OWN realized excursions.
    The stance bar is the median excursion of the whole run, which cannot be known
    until the run is over — so scoring waits, rather than shipping a bar the tape
    never reaches."""
    exc = sorted(r["_real"]["excursion_sigma"] for r in recs if r.get("_real"))
    bar = exc[len(exc) // 2] if exc else 1.0
    for r in recs:
        if r.get("_v") and r.get("_real"):
            r.update(score(r["_v"], r["_real"], bar))
        r.pop("_v", None); r.pop("_real", None)
    print(f"\nstance bar (median realized excursion): {bar}σ")
    return recs


# --------------------------------------------------------------- summary -----
def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def _rate(xs):
    xs = [x for x in xs if isinstance(x, bool)]
    return (round(sum(xs) / len(xs), 3), len(xs)) if xs else (None, 0)


def summarize(recs: list[dict]) -> dict:
    """Arm vs arm on the four things that matter: what it cost, how long it
    thought, how sure it said it was, and whether it was right."""
    out = {}
    for arm in ("tools_on", "tools_off"):
        a = [r for r in recs if r["arm"] == arm and not r.get("error")]
        errs = len([r for r in recs if r["arm"] == arm and r.get("error")])
        dir_hit, dir_n = _rate([r.get("dir_hit") for r in a])
        st_hit, st_n = _rate([r.get("stance_hit") for r in a])
        mag_hit, mag_n = _rate([r.get("mag_hit") for r in a])
        tok = [(r.get("cache_read", 0) + r.get("cache_write", 0)
                + r.get("in", 0) + r.get("out", 0)) for r in a]
        out[arm] = {
            "n": len(a), "errors": errs,
            "tokens_per_call": _mean(tok),
            "cache_read": _mean([r.get("cache_read") for r in a]),
            "out_tokens": _mean([r.get("out") for r in a]),
            "wall_s": _mean([r.get("wall_s") for r in a]),
            "conviction": _mean([r.get("conviction") for r in a]),
            "fire_rate": _rate([bool(r.get("fired")) for r in a])[0],
            "dir_hit": dir_hit, "dir_n": dir_n,
            "stance_hit": st_hit, "stance_n": st_n,
            "mag_hit": mag_hit, "mag_n": mag_n,
            "self_consistency": _self_consistency(a),
        }
    out["agreement_between_arms"] = _cross_agreement(recs)
    return out


def _self_consistency(a: list[dict]) -> float | None:
    """Within one arm, how often the k samples of a scene agree with their own
    modal answer. A shallower think that is also less STABLE is a real cost even
    when its hit rate looks fine — the tower votes, and votes need reproducibility."""
    by_scene: dict[tuple, list] = {}
    for r in a:
        by_scene.setdefault((r["day"], r["ts"]), []).append(
            (bool(r.get("fired")), r.get("direction")))
    fracs = []
    for v in by_scene.values():
        if len(v) < 2:
            continue
        fracs.append(max(v.count(x) for x in set(v)) / len(v))
    return round(statistics.mean(fracs), 3) if fracs else None


def _cross_agreement(recs: list[dict]) -> float | None:
    """Do the arms reach the same call on the same scene? Cheap and identical is
    the outcome that ends this question; cheap and DIFFERENT is the one that
    needs the hit rates to break the tie."""
    by_scene: dict[tuple, dict] = {}
    for r in recs:
        if r.get("error"):
            continue
        by_scene.setdefault((r["day"], r["ts"]), {}).setdefault(r["arm"], []).append(
            (bool(r.get("fired")), r.get("direction")))
    agree = []
    for arms in by_scene.values():
        if len(arms) < 2:
            continue
        mode = {k: max(set(v), key=v.count) for k, v in arms.items()}
        agree.append(mode.get("tools_on") == mode.get("tools_off"))
    return round(sum(agree) / len(agree), 3) if agree else None


def print_table(s: dict) -> None:
    on, off = s["tools_on"], s["tools_off"]
    rows = [
        ("calls scored", on["n"], off["n"]),
        ("errors", on["errors"], off["errors"]),
        ("tokens / call", on["tokens_per_call"], off["tokens_per_call"]),
        ("  cached read block", on["cache_read"], off["cache_read"]),
        ("  output tokens", on["out_tokens"], off["out_tokens"]),
        ("wall seconds", on["wall_s"], off["wall_s"]),
        ("stated conviction", on["conviction"], off["conviction"]),
        ("fire rate", on["fire_rate"], off["fire_rate"]),
        (f"direction hit (n={on['dir_n']}/{off['dir_n']})", on["dir_hit"], off["dir_hit"]),
        (f"stance hit (n={on['stance_n']}/{off['stance_n']})", on["stance_hit"], off["stance_hit"]),
        (f"magnitude hit (n={on['mag_n']}/{off['mag_n']})", on["mag_hit"], off["mag_hit"]),
        ("self-consistency", on["self_consistency"], off["self_consistency"]),
    ]
    w = max(len(r[0]) for r in rows) + 2
    print(f"\n{'':<{w}}{'tools ON':>12}{'tools OFF':>12}{'delta':>12}")
    print("-" * (w + 36))
    for name, a, b in rows:
        d = ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
            d = f"{(b - a) / a * 100:+.0f}%" if name != "errors" else f"{b - a:+d}"
        print(f"{name:<{w}}{str(a):>12}{str(b):>12}{d:>12}")
    print(f"\narms agree on the same scene: {s['agreement_between_arms']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--scenes", type=int, default=SCENES_PER_DAY)
    p.add_argument("--samples", type=int, default=SAMPLES)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    scenes = pick_scenes(a.days, a.scenes)
    print(f"scenes: {len(scenes)} across {len({s['day'] for s in scenes})} days")
    for s in scenes:
        print(f"  {s['day']} {s['ts'][11:16]}  {s['stratum']:<14} {s['why']}")
    if a.dry_run:
        print(f"\ndry run — would make {len(scenes) * a.samples * 2} calls")
        return

    recs = run(scenes, a.samples, wt._model())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    raw = OUT_DIR / f"run-{stamp}.jsonl"
    raw.write_text("".join(json.dumps(r) + "\n" for r in recs))
    s = summarize(recs)
    (OUT_DIR / f"summary-{stamp}.json").write_text(json.dumps(s, indent=2))
    print_table(s)
    print(f"\nraw: {raw}")


if __name__ == "__main__":
    main()
