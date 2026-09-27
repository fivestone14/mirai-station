"""The events and shocks family (labels/events_shocks.py): each label's sentence at its verdicts and their
boundaries, its omissions, point in time, and each gate both ways.

The calendar labels read the shipped calendar/events.json on real days: 09-29 (job openings and consumer
confidence at 10:00, two governors after noon), 10-01 (claims before the open, ISM at 10:00 with a governor,
four speakers), 10-02 (the jobs report), 10-13 (nothing), 10-20 (nothing, in the Fed's quiet period) and the
10-28 decision. Every day's own ruler and the normal-day sigma are 100 points, so a sigma is a point / 100.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from conftest import at, bars_from_closes, make_row
from spx_jev.labels.events_shocks import DARK, build_events_shocks_labels
from spx_jev.labels.rulers import SigmaRuler

NORMAL = {f"2026-09-{d:02d}": SigmaRuler(100.0, "anchor") for d in range(21, 26)}


def minute(hh: int, mm: int) -> int:
    """The bar index of a clock minute: 09:30 is 0."""
    return (hh - 9) * 60 + mm - 30


def steps(marks: list[tuple[int, int, float]], until: tuple[int, int] = (15, 59)) -> list[float]:
    """Closes held at each mark's price from its minute on: [(9, 30, 7700.0), (10, 0, 7716.0)]."""
    out, price = [], marks[0][2]
    points = {minute(h, m): p for h, m, p in marks}
    for i in range(minute(*until) + 1):
        price = points.get(i, price)
        out.append(price)
    return out


def scene_at(scene_factory, now, closes, *, prior_rulers=NORMAL, prior_bars=None, market=None, state_dir=None,
             anchor_row=True, spot=None, **row_over):
    day = now.date().isoformat()
    bars = bars_from_closes(closes, day=day)
    done = [float(b["close"]) for b in bars if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]
    rows = [make_row(at(9, 31, day), 7700.0, sigma=100.0)] if anchor_row else []
    scene = scene_factory(now, bars, rows_before=rows, prior_bars=prior_bars, spot=spot if spot is not None else done[-1], market=market,
                          row_over={"sigma": 100.0, "sigma_anchor": 100.0, "sigma_live": 100.0, **row_over})
    return replace(scene, prior_rulers=prior_rulers, state_dir=state_dir)


def labels(scene):
    ls = build_events_shocks_labels(scene)
    return {f"{g}.{k}": v for g, kv in ls.state.items() for k, v in kv.items()}, ls.omitted, ls.gates


FLAT = steps([(9, 30, 7700.0)])


def test_the_headline_gate_sleeps_while_its_feed_is_dark(scene_factory):
    assert labels(scene_at(scene_factory, at(12, 2, "2026-10-13"), FLAT))[2]["news_headline"] == DARK["news.intraday_headline"]


# ---- context.event_clock

def test_the_event_clock_names_every_row_of_the_day_against_its_window(scene_factory):
    state, _, gates = labels(scene_at(scene_factory, at(9, 38, "2026-10-01"), FLAT))
    assert state["context.event_clock"] == (
        "the weekly jobless claims report was expected at 08:30, before the open; "
        "the ISM manufacturing report is expected at 10:00, 22 minutes from now, inside the 60-minute due window; "
        "a Fed governor speaks at 10:00, 22 minutes from now, inside the 60-minute speaker window; "
        "the Fed vice chair speaks at 13:30, 232 minutes from now, beyond the 60-minute speaker window; "
        "the Fed's vice chair for supervision speaks at 15:00, 322 minutes from now, beyond the 60-minute speaker window; "
        "a Fed governor speaks at 15:30 with audience questions, 352 minutes from now, beyond the 60-minute speaker window; "
        "the Fed is not in its pre-meeting quiet period")
    assert gates["event_clock"] is None


def test_the_event_clock_says_what_came_out_and_who_is_speaking_now(scene_factory):
    state, _, _ = labels(scene_at(scene_factory, at(12, 50, "2026-09-29"), FLAT))
    assert state["context.event_clock"] == (
        "the Conference Board's consumer confidence report came out at 10:00, 170 minutes ago, beyond the 120-minute digest window; "
        "the job openings report came out at 10:00, 170 minutes ago, beyond the 120-minute digest window; "
        "a Fed governor has been speaking since 12:40 with audience questions, inside the 60-minute speaker window; "
        "a Fed governor speaks at 13:25, 35 minutes from now, inside the 60-minute speaker window; "
        "the Fed is not in its pre-meeting quiet period")


def test_the_due_and_digest_windows_are_inclusive_and_the_gate_sleeps_past_the_digest(scene_factory):
    fed = scene_at(scene_factory, at(13, 0, "2026-10-28"), FLAT)
    assert "the Fed's rate decision comes out at 14:00, 60 minutes from now, inside the 60-minute due window" in labels(fed)[0]["context.event_clock"]
    early = replace(fed, now=at(12, 59, "2026-10-28", ss=30))
    assert "61 minutes from now, beyond the 60-minute due window" in labels(early)[0]["context.event_clock"]
    jobs = scene_at(scene_factory, at(10, 30, "2026-10-02"), FLAT)
    state, _, gates = labels(jobs)
    assert state["context.event_clock"] == ("the jobs report came out at 08:30, before the open; nothing is scheduled in the session; "
                                            "the Fed is not in its pre-meeting quiet period")
    assert gates["event_clock"] is None
    assert labels(replace(jobs, now=at(10, 31, "2026-10-02")))[2]["event_clock"] == \
        "every scheduled event today came out more than 120 minutes ago"


def test_an_empty_day_is_written_and_its_gate_sleeps_and_the_quiet_period_is_named(scene_factory):
    state, _, gates = labels(scene_at(scene_factory, at(10, 32, "2026-10-13"), FLAT))
    assert state["context.event_clock"] == "nothing is scheduled today; the Fed is not in its pre-meeting quiet period"
    assert gates["event_clock"] == "nothing is on the event calendar today"
    state, _, _ = labels(scene_at(scene_factory, at(10, 32, "2026-10-20"), FLAT))
    assert state["context.event_clock"] == "nothing is scheduled today; the Fed is in its pre-meeting quiet period"


def test_past_the_calendars_last_day_the_calendar_labels_are_omitted_and_their_gates_sleep(scene_factory):
    _, omitted, gates = labels(scene_at(scene_factory, at(10, 32, "2027-01-05"), FLAT))
    why = "the event calendar (calendar/events.json) is kept only through 2026-12-31: extend it"
    assert omitted["context.event_clock"] == omitted["event.release_clock_10m"] == why
    assert gates["event_clock"] == gates["release_in_lane"] == why


# ---- event.release_clock_10m

def test_a_release_and_a_speaker_due_inside_the_lanes_window(scene_factory):
    state, _, gates = labels(scene_at(scene_factory, at(9, 52, "2026-10-01"), FLAT))
    assert state["event.release_clock_10m"] == (
        "the ISM manufacturing report is expected at 10:00, 8 minutes from now, inside this 10-minute window; "
        "a Fed governor speaks at 10:00, 8 minutes from now, inside this 10-minute window")
    assert gates["release_in_lane"] is None
    state, _, _ = labels(scene_at(scene_factory, at(9, 55, "2026-09-29"), FLAT))
    assert state["event.release_clock_10m"] == (
        "the Conference Board's consumer confidence report comes out at 10:00, 5 minutes from now, inside this 10-minute window; "
        "the job openings report comes out at 10:00, 5 minutes from now, inside this 10-minute window; "
        "no Fed speaker is scheduled this morning")


def test_the_lanes_window_is_ten_minutes_either_side_inclusive(scene_factory):
    s = scene_at(scene_factory, at(9, 50, "2026-09-29"), FLAT)
    assert "5 minutes" not in labels(s)[0]["event.release_clock_10m"]
    assert labels(s)[0]["event.release_clock_10m"].startswith(
        "the Conference Board's consumer confidence report comes out at 10:00, 10 minutes from now, inside this 10-minute window")
    state, _, gates = labels(replace(s, now=at(9, 49, "2026-09-29", ss=30)))
    assert state["event.release_clock_10m"] == (
        "nothing scheduled starts within 10 minutes of now; next, the Conference Board's consumer confidence report comes out "
        "at 10:00, 11 minutes from now; no Fed speaker is scheduled this morning")
    assert gates["release_in_lane"] == "nothing scheduled is due within 10 minutes or started within the last 10"
    state, _, gates = labels(replace(s, now=at(10, 10, "2026-09-29")))
    assert "the job openings report came out at 10:00, 10 minutes ago, inside the last 10 minutes" in state["event.release_clock_10m"]
    assert gates["release_in_lane"] is None
    assert labels(replace(s, now=at(10, 10, "2026-09-29", ss=30)))[2]["release_in_lane"] is not None


def test_an_afternoon_with_nothing_left(scene_factory):
    state, _, _ = labels(scene_at(scene_factory, at(14, 0, "2026-10-13"), FLAT))
    assert state["event.release_clock_10m"] == ("nothing scheduled starts within 10 minutes of now; nothing more is scheduled in the "
                                                "session; no more Fed speakers are scheduled today")
