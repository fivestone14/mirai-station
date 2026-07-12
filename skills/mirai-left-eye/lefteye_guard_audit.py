"""GUARD AUDIT — make a dead guard impossible to hide.

The 2026-07-12 review found slide E (`reconcile_sign`) had been running for its whole life
with trip level FLOW_CONFLICT = 0.6 against an options aggressor tape whose measured live
range is [−0.104, +0.441]. It never had the RANGE to condemn. It fired 0 times in 671 rows —
and, worse, stamped `sign_agrees=True, sign_confidence=1.0` on every non-null read, so the
diary is full of a safety check reporting success while measuring nothing.

That had already happened once before: FLOW_CONFLICT_LOB was cut 0.6 → 0.25 for exactly the
same reason ("verified dead-as-0.6"). Twice, and nothing in the system noticed either time,
because NOTHING ANYWHERE COUNTED HOW OFTEN A GUARD FIRES.

This module is that counter. A guard is DEAD when, over enough scans to be a fact rather than
a small sample, it has never tripped AND its own input statistic never even approached its
trip level. The distinction matters: "never tripped" alone is consistent with a calm market,
but "never tripped and never came within 2× of tripping" means the threshold is unreachable
on this source and the guard is decorative.

Read-only over the diary. No I/O beyond the rows the caller hands in.
"""
from __future__ import annotations

from typing import Optional

try:
    from lefteye_gex_box import FLOW_CONFLICT, FLOW_CONFLICT_LOB
except Exception:                                  # audit must run even if the engine won't
    FLOW_CONFLICT, FLOW_CONFLICT_LOB = 0.6, 0.25

# (name, the guard's own input statistic, the trip level it is compared against, the
#  flow_kind that selects this guard on a given scan)
GUARDS = [
    ("slide_E_options", "aggressor_flow", FLOW_CONFLICT, "options"),
    ("slide_E_lob", "veto_flow", FLOW_CONFLICT_LOB, "lob"),
]

DEAD_GUARD_MIN_N = 200       # enough evaluations that "never fired" is a fact, not a quiet week
DEAD_GUARD_HEADROOM = 0.5    # the stat's TYPICAL reading never got within 2× of its trip level.
                             # Judged on p95, not max: one outlier must not rescue a guard. The
                             # real slide-E case is exactly this shape — the options tape touched
                             # 0.441 once (73% of the 0.6 trip) but its p95 is 0.075, i.e. 12% of
                             # the trip level. On `max` that reads "reachable, just unlucky"; on
                             # p95 it reads what it is: the threshold lives in a different range
                             # from the statistic, and the guard is decorative.


def _gv(row: dict) -> dict:
    return row.get("gex_views") or {}


def audit(rows: list[dict]) -> list[dict]:
    """One verdict per guard. A guard is DEAD when it has been evaluated enough times, has
    never tripped, and its input never reached DEAD_GUARD_HEADROOM of its trip level — i.e.
    it is not agreeing with the map, it is SILENT, and the stream of `agrees=True` it emits
    is not evidence of anything.

    Also flags OVERSENSITIVE (trips on essentially every scan — a guard that always fires
    carries as little information as one that never does) and reports p95/max headroom so a
    threshold can be retuned against the statistic's own realised distribution rather than
    against a number somebody once guessed."""
    out: list[dict] = []
    for name, stat_key, trip, kind in GUARDS:
        heads: list[float] = []
        n_eval = n_trip = 0
        for r in rows:
            gv = _gv(r)
            if gv.get("veto_flow_source") and kind:
                # only score a guard on the scans where its own tape was the one consumed
                src = "lob" if gv.get("veto_flow_source") == "lob" else "options"
                if src != kind:
                    continue
            v = gv.get(stat_key)
            if v is None:
                continue
            n_eval += 1
            if trip:
                heads.append(abs(float(v)) / trip)
            if gv.get("sign_trip") or gv.get("sign_agrees") is False:
                n_trip += 1
        if not n_eval:
            out.append({"guard": name, "status": "UNCOMPUTED", "n_eval": 0,
                        "note": "no scan ever fed this guard its statistic"})
            continue
        heads.sort()
        mx = heads[-1] if heads else None
        p95 = heads[int(0.95 * (len(heads) - 1))] if heads else None
        rate = n_trip / n_eval

        # A GUARD THAT HAS NEVER SAID NO NEVER READS GREEN. That is the whole doctrine here:
        # "0 trips" is not evidence of a calm market, it is the absence of evidence that the
        # guard works at all — and for 671 rows the system read it as the former.
        status = "OK"
        note = ""
        if n_trip == 0:
            if mx is not None and mx >= 1.0:
                # The statistic CROSSED its trip level and the guard still stayed silent, so a
                # PRECONDITION — not the threshold — is deciding the outcome (slide E's options
                # arm only fires on a POSITIVE tape; its LOB arm only fires when the regime is
                # already long_gamma). This is not automatically a bug: the LOB gate is
                # long-gamma-only BY DESIGN, because the freight-train test only matters when
                # you are about to trust a pin. Report the fact, name the question, and let a
                # human rule — an audit that cries wolf gets muted, and then it is worth as
                # little as the guard it was watching.
                status = "GATED"
                note = (f"the statistic reached {mx:.0%} of its trip level ({trip}) yet the "
                        f"guard never fired in {n_eval} evaluations — the threshold is "
                        f"REACHABLE, so a PRECONDITION is deciding this guard's verdict, not "
                        f"its threshold. Retuning the threshold would change nothing. Confirm "
                        f"the precondition is the intended one, and that the scenario it gates "
                        f"on has actually occurred.")
            elif n_eval >= DEAD_GUARD_MIN_N and p95 is not None and p95 < DEAD_GUARD_HEADROOM:
                status = "DEAD"
                note = (f"{n_eval} evaluations, 0 trips, and 95% of readings never exceeded "
                        f"{p95:.0%} of the trip level ({trip}; peak {mx:.0%}). The threshold "
                        f"lives in a different range from the statistic — this guard CANNOT "
                        f"FIRE on this source, so every 'agrees' it has emitted is silence, "
                        f"not assent. Retune against the statistic's own realised "
                        f"distribution, behind its own flag, with its own A/B.")
            elif n_eval >= DEAD_GUARD_MIN_N:
                status = "UNFIRED"
                note = (f"0 trips in {n_eval} evaluations (peak {mx:.0%} of the trip level). "
                        f"Reachable, never reached — a guard with no NO in its history has no "
                        f"demonstrated ability to say one.")
            else:
                status = "UNPROVEN"
                note = (f"0 trips, but only {n_eval} evaluations (< {DEAD_GUARD_MIN_N}) — too "
                        f"few to call it dead, and far too few to call it working. NOT ok.")
        elif rate >= 0.95:
            status = "OVERSENSITIVE"
            note = f"trips on {rate:.0%} of scans — a guard that always fires says nothing."
        out.append({"guard": name, "status": status, "n_eval": n_eval, "n_trip": n_trip,
                    "trip_rate": round(rate, 4), "trip_level": trip,
                    "max_headroom": round(mx, 3) if mx is not None else None,
                    "p95_headroom": round(p95, 3) if p95 is not None else None,
                    "note": note})
    return out


def render(verdicts: list[dict]) -> str:
    """One line per guard for the nightly card and the health check. A DEAD guard must be
    impossible to skim past — it is a red line, not a footnote."""
    lines = []
    for v in verdicts:
        mark = {"DEAD": "🔴 DEAD", "GATED": "🟡 GATED", "UNFIRED": "🟡 UNFIRED",
                "OVERSENSITIVE": "🟡 OVERSENSITIVE", "UNPROVEN": "🟡 UNPROVEN",
                "UNCOMPUTED": "⚪ UNCOMPUTED", "OK": "🟢 ok"}.get(v["status"], v["status"])
        head = f"{mark}  {v['guard']}"
        if v.get("n_eval"):
            head += (f"  — {v['n_trip']}/{v['n_eval']} trips"
                     f"  (max headroom {v.get('max_headroom')}× of {v.get('trip_level')})")
        lines.append(head)
        if v.get("note"):
            lines.append(f"        {v['note']}")
    return "\n".join(lines)
