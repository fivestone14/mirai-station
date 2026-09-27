"""The events and shocks family (labels/events_shocks.py): each label's sentence at its verdicts and their
boundaries, its omissions, point in time, and each gate both ways.

The calendar labels read the shipped calendar/events.json on real days: 09-22 (the vice chair for supervision
at 09:30 with questions), 09-29 (job openings and consumer
confidence at 10:00, two governors after noon), 10-01 (claims before the open, ISM at 10:00 with a governor,
four speakers), 10-02 (the jobs report), 10-13 (nothing), 10-20 (nothing, in the Fed's quiet period) and the
10-28 decision; 10-06 lists only the trade balance and 09-30 ADP beside GDP and PCE, minor releases
the session leaves out. Every day's own ruler and the normal-day sigma are 100 points, so a sigma is a point / 100.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

from conftest import at, bars_from_closes, make_row, prior_sessions
from spx_jev.labels.events_shocks import DARK, LABELS, build_events_shocks_labels
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.state_builder import MarketContext

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


def test_the_family_writes_or_omits_every_label_it_owns_and_sleeps_the_headline_gate(scene_factory):
    state, omitted, gates = labels(scene_at(scene_factory, at(12, 2, "2026-10-13"), FLAT))
    assert set(state) | set(omitted) | set(DARK) == set(LABELS)
    assert gates["news_headline"] == DARK["news.intraday_headline"]


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


def test_the_press_conference_is_under_way_until_its_end_then_digested(scene_factory):
    s = scene_at(scene_factory, at(14, 45, "2026-10-28"), FLAT)
    state, _, gates = labels(s)
    assert state["context.event_clock"] == (
        "the Fed's rate decision came out at 14:00, 45 minutes ago, inside the 120-minute digest window; "
        "the Fed chair's press conference is under way until 15:30, inside the 60-minute due window; "
        "the Fed is in its pre-meeting quiet period")
    assert gates["event_clock"] is None
    assert ("the Fed chair's press conference began at 14:30, 60 minutes ago, inside the 120-minute digest window"
            in labels(replace(s, now=at(15, 30, "2026-10-28")))[0]["context.event_clock"])


def test_an_empty_day_is_written_and_its_gate_sleeps_and_the_quiet_period_is_named(scene_factory):
    state, _, gates = labels(scene_at(scene_factory, at(10, 32, "2026-10-13"), FLAT))
    assert state["context.event_clock"] == "nothing is scheduled today; the Fed is not in its pre-meeting quiet period"
    assert gates["event_clock"] == "nothing is on the event calendar today"
    state, _, _ = labels(scene_at(scene_factory, at(10, 32, "2026-10-20"), FLAT))
    assert state["context.event_clock"] == "nothing is scheduled today; the Fed is in its pre-meeting quiet period"


def test_the_minor_releases_before_the_open_leave_the_session_as_it_was(scene_factory):
    state, _, gates = labels(scene_at(scene_factory, at(10, 32, "2026-10-06"), FLAT))
    assert state["context.event_clock"] == "nothing is scheduled today; the Fed is not in its pre-meeting quiet period"
    assert gates["event_clock"] == "nothing is on the event calendar today"
    clock = labels(scene_at(scene_factory, at(9, 38, "2026-09-30"), FLAT))[0]["context.event_clock"]
    assert clock.startswith("the GDP report came out at 08:30, before the open; the PCE inflation report came out at 08:30")
    assert "ADP" not in clock


def test_past_the_calendars_last_day_the_calendar_labels_are_omitted_and_their_gates_sleep(scene_factory):
    _, omitted, gates = labels(scene_at(scene_factory, at(10, 32, "2027-01-05"), FLAT))
    why = "the event calendar (calendar/events.json) is kept only through 2026-12-31: extend it"
    for path in ("context.event_clock", "event.release_clock_10m", "event.reaction", "event.statement_and_presser"):
        assert omitted[path] == why
    assert gates["event_clock"] == gates["release_in_lane"] == gates["move_reaction_path"] == why


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


def test_a_speaker_still_speaking_is_named_until_the_speech_ends(scene_factory):
    s = scene_at(scene_factory, at(9, 45, "2026-09-22"), FLAT)
    assert labels(s)[0]["event.release_clock_10m"] == (
        "nothing scheduled starts within 10 minutes of now; nothing more is scheduled in the session; the Fed's vice chair "
        "for supervision has been speaking since 09:30 with audience questions")
    assert labels(replace(s, now=at(10, 29, "2026-09-22", ss=30)))[0]["event.release_clock_10m"].endswith(
        "the Fed's vice chair for supervision has been speaking since 09:30 with audience questions")
    assert labels(replace(s, now=at(10, 30, "2026-09-22")))[0]["event.release_clock_10m"].endswith(
        "no Fed speaker is scheduled this morning")


def test_an_afternoon_with_nothing_left(scene_factory):
    state, _, _ = labels(scene_at(scene_factory, at(14, 0, "2026-10-13"), FLAT))
    assert state["event.release_clock_10m"] == ("nothing scheduled starts within 10 minutes of now; nothing more is scheduled in the "
                                                "session; no more Fed speakers are scheduled today")


# ---- event.reaction

JOLTS_DAY = "2026-09-29"


def reaction_scene(scene_factory, now, spot_at_now, first_end=7716.0, day=JOLTS_DAY, start=(10, 0)):
    """Price 7700 into the start, ``first_end`` 15 minutes on, ``spot_at_now`` from 11:00."""
    h, m = start
    return scene_at(scene_factory, now, steps([(9, 30, 7700.0), (h, m, first_end - 8.0), (h, m + 14, first_end), (11, 0, spot_at_now)]))


def test_a_release_whose_move_extends_past_the_extension_rule(scene_factory):
    state, _, gates = labels(reaction_scene(scene_factory, at(11, 32, JOLTS_DAY), 7722.0))
    assert state["event.reaction"] == (
        "the reaction starts at 10:00 with the Conference Board's consumer confidence report and the job openings report; in its "
        "first 15 minutes price rose 0.16 normal-day sigma, "
        "past the 0.15 normal-day sigma reaction rule; since then it pushed 0.06 normal-day sigma further the same way, past the "
        "0.05 normal-day sigma extension rule, and has given back none of it; this was scheduled economic data")
    assert gates["move_reaction_path"] is None


def test_the_reaction_rule_and_the_extension_and_half_lines_at_their_boundaries(scene_factory):
    now = at(11, 32, JOLTS_DAY)
    state, _, gates = labels(reaction_scene(scene_factory, now, 7720.0, first_end=7715.0))
    assert "price rose 0.15 normal-day sigma, past the 0.15 normal-day sigma reaction rule" in state["event.reaction"]
    assert "pushed 0.05 normal-day sigma further the same way, short of the 0.05 normal-day sigma extension rule" in state["event.reaction"]
    assert gates["move_reaction_path"] is None
    state, _, gates = labels(reaction_scene(scene_factory, now, 7714.0, first_end=7714.0))
    assert "price rose 0.14 normal-day sigma, short of the 0.15 normal-day sigma reaction rule" in state["event.reaction"]
    assert gates["move_reaction_path"] == "the first reaction moved 0.14 normal-day sigma, less than the 0.15 normal-day sigma reaction rule"
    held = labels(reaction_scene(scene_factory, now, 7708.0))[0]["event.reaction"]
    assert "it has given back 50% of it, within the half line, and has not crossed back through where the reaction started" in held
    faded = labels(reaction_scene(scene_factory, now, 7707.9))[0]["event.reaction"]
    assert "it has given back 51% of it, past the half line, and has not crossed back" in faded
    reversed_ = labels(reaction_scene(scene_factory, now, 7698.0))[0]["event.reaction"]
    assert ("it has given back all of it and crossed back through where the reaction started, now 0.02 normal-day sigma the other "
            "side of it") in reversed_


def test_a_burst_on_a_quiet_day_starts_a_reaction_and_is_named_unscheduled(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), size=16.0, after=16.0)
    state, _, gates = labels(s)
    assert state["event.reaction"].startswith("the reaction starts at 11:40 with a sudden burst; in its first 15 minutes price rose ")
    assert state["event.reaction"].endswith("; this was an unscheduled burst")
    assert gates["move_reaction_path"] is None


def test_on_a_fed_day_the_reaction_starts_at_the_press_conference(scene_factory):
    s = fomc_scene(scene_factory, at(15, 2, FOMC_DAY), statement=7716.0, presser_end=7674.0, spot=7664.0)
    assert labels(s)[0]["event.reaction"] == (
        "the reaction starts at 14:30 with the Fed chair's press conference (the Fed's rate decision at 14:00 moved price 0.16 "
        "normal-day sigma in its first 15 minutes); in its first 15 minutes price fell 0.26 normal-day sigma, past the 0.15 "
        "normal-day sigma reaction rule; since then it pushed 0.10 normal-day sigma further the same way, past the 0.05 normal-day "
        "sigma extension rule, and has given back none of it; this was a scheduled Fed event")


def test_the_reaction_waits_for_its_first_fifteen_minutes_and_never_reads_a_later_bar(scene_factory):
    s = reaction_scene(scene_factory, at(10, 14, JOLTS_DAY, ss=30), 7722.0)
    _, omitted, gates = labels(s)
    why = "no release, Fed remarks or burst in the last 120 minutes whose first 15 minutes are over"
    assert omitted["event.reaction"] == why and gates["move_reaction_path"] == why
    at_15 = labels(reaction_scene(scene_factory, at(10, 15, JOLTS_DAY), 7722.0))[0]["event.reaction"]
    assert "price rose 0.16 normal-day sigma" in at_15 and "has given back none of it" in at_15   # spot is the 10:14 close


def test_the_reaction_needs_the_normal_day_sigma(scene_factory):
    s = replace(reaction_scene(scene_factory, at(11, 32, JOLTS_DAY), 7722.0), prior_rulers=dict(list(NORMAL.items())[:4]))
    _, omitted, gates = labels(s)
    assert omitted["event.reaction"] == "needs 5 prior sessions with a morning anchor for the normal-day sigma"
    assert gates["move_reaction_path"] == omitted["event.reaction"]


# ---- event.statement_and_presser

FOMC_DAY = "2026-10-28"


def fomc_scene(scene_factory, now, statement, presser_end, spot):
    """7700 into 14:00, ``statement`` by 14:15, back to 7700 by 14:30, ``presser_end`` by 14:45, ``spot`` from 14:50."""
    return scene_at(scene_factory, now, steps([(9, 30, 7700.0), (14, 14, statement), (14, 29, 7700.0), (14, 44, presser_end),
                                               (14, 50, spot)]))


def test_the_press_conference_went_the_other_way_from_the_statement(scene_factory):
    s = fomc_scene(scene_factory, at(15, 2, FOMC_DAY), statement=7716.0, presser_end=7690.0, spot=7638.0)
    assert labels(s)[0]["event.statement_and_presser"] == (
        "in the 15 minutes after the Fed's 14:00 statement price rose 0.16 normal-day sigma, past the 0.05 statement-quiet line; "
        "since the chair's press conference began at 14:30 price has fallen 0.62 normal-day sigma, the other way, past the "
        "0.15 presser rule")


def test_the_statement_quiet_line_and_the_presser_rule_at_their_boundaries(scene_factory):
    now = at(15, 2, FOMC_DAY)
    quiet = labels(fomc_scene(scene_factory, now, statement=7704.0, presser_end=7690.0, spot=7684.0))[0]["event.statement_and_presser"]
    assert ("price rose 0.04 normal-day sigma, inside the 0.05 statement-quiet line; since the chair's press conference began at 14:30 "
            "price has fallen 0.16 normal-day sigma, past the 0.15 presser rule") in quiet
    at_line = labels(fomc_scene(scene_factory, now, statement=7705.0, presser_end=7710.0, spot=7715.0))[0]["event.statement_and_presser"]
    assert "rose 0.05 normal-day sigma, past the 0.05 statement-quiet line" in at_line
    assert at_line.endswith("price has risen 0.15 normal-day sigma, the same way, within the 0.15 presser rule")


def test_the_statement_and_presser_needs_a_fed_day_after_the_press_conference_began(scene_factory):
    assert labels(scene_at(scene_factory, at(15, 2, "2026-10-13"), FLAT))[1]["event.statement_and_presser"] == "no Fed rate decision today"
    early = fomc_scene(scene_factory, at(14, 20, FOMC_DAY), statement=7716.0, presser_end=7690.0, spot=7638.0)
    assert labels(early)[1]["event.statement_and_presser"] == "the Fed chair's press conference starts at 14:30, after this read"
    # at 14:40 the 14:44 fall has not happened yet: price has not moved since the press conference began
    s = fomc_scene(scene_factory, at(14, 40, FOMC_DAY), statement=7716.0, presser_end=7690.0, spot=7638.0)
    assert labels(s)[0]["event.statement_and_presser"].endswith("price has not moved, within the 0.15 presser rule")


# ---- news.morning_brief

BRIEF_DAY = "2026-09-25"


def brief_scene(scene_factory, tmp_path, now, direction=0.3, confidence=0.45, gap_points=30.0, lines=None):
    """The 09-25 learning log as the market-expectation job writes it, and a settled open ``gap_points`` over
    yesterday's close."""
    folder = tmp_path / "market_expectation"
    folder.mkdir(exist_ok=True)
    if lines is None:
        lines = [{"kind": "brief", "reason": "morning", "ts": "2026-09-25T09:00:01.728808-04:00",
                  "overall": {"direction": direction, "magnitude": 0.7, "confidence": confidence}, "drift_cosine": None},
                 {"kind": "eod_score", "predicted_dir": direction, "realized_move": 36.39, "won": True, "reliability": 0.0725}]
    (folder / f"learning-{BRIEF_DAY}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    closes = steps([(9, 30, 7704.13 + gap_points)], until=(10, 30))
    return scene_at(scene_factory, now, closes, state_dir=tmp_path, prior_close=7704.13)


def test_the_brief_restates_the_gap(scene_factory, tmp_path):
    state, _, _ = labels(brief_scene(scene_factory, tmp_path, at(9, 35, BRIEF_DAY)))
    assert state["news.morning_brief"] == (
        "the 09:00 morning brief leans up 0.3 on a -1 to +1 scale, past the 0.2 direction floor, with confidence 0.45, past the "
        "0.3 confidence floor; this morning's gap was 0.30 sigma up, past the 0.15 sigma gap rule, so the brief leans the way "
        "the gap went")


def test_the_brief_verdicts_at_their_floors_and_the_gap_rule(scene_factory, tmp_path):
    now = at(9, 35, BRIEF_DAY)
    against = labels(brief_scene(scene_factory, tmp_path, now, direction=-0.2, confidence=0.3))[0]["news.morning_brief"]
    assert against.startswith("the 09:00 morning brief leans down 0.2 on a -1 to +1 scale, past the 0.2 direction floor, with "
                              "confidence 0.3, past the 0.3 confidence floor;")
    assert against.endswith("so the brief leans against the way the gap went")
    weak = labels(brief_scene(scene_factory, tmp_path, now, direction=0.19))[0]["news.morning_brief"]
    assert "leans up 0.19 on a -1 to +1 scale, under the 0.2 direction floor" in weak and weak.endswith("so the brief counts as no view")
    unsure = labels(brief_scene(scene_factory, tmp_path, now, confidence=0.29))[0]["news.morning_brief"]
    assert "with confidence 0.29, under the 0.3 confidence floor" in unsure and unsure.endswith("so the brief counts as no view")
    at_rule = labels(brief_scene(scene_factory, tmp_path, now, gap_points=-15.0))[0]["news.morning_brief"]
    assert at_rule.endswith("this morning's gap was 0.15 sigma down, past the 0.15 sigma gap rule, so the brief leans against the way "
                            "the gap went")
    inside = labels(brief_scene(scene_factory, tmp_path, now, gap_points=14.0))[0]["news.morning_brief"]
    assert inside.endswith("0.14 sigma up, inside the 0.15 sigma gap rule, so the brief takes a side the gap did not")


def test_the_brief_is_omitted_without_a_morning_entry_known_at_the_read(scene_factory, tmp_path):
    now = at(9, 35, BRIEF_DAY)
    assert labels(replace(brief_scene(scene_factory, tmp_path, now), state_dir=None))[1]["news.morning_brief"] == \
        "no state folder to read the morning brief from"
    redive = [{"kind": "brief", "reason": "redive", "ts": "2026-09-25T09:10:00-04:00", "overall": {"direction": 0.3, "confidence": 0.45}}]
    assert labels(brief_scene(scene_factory, tmp_path, now, lines=redive))[1]["news.morning_brief"] == \
        "no morning brief in market_expectation/learning-2026-09-25.jsonl"
    late = [{"kind": "brief", "reason": "morning", "ts": "2026-09-25T09:40:00-04:00", "overall": {"direction": 0.3, "confidence": 0.45}}]
    assert labels(brief_scene(scene_factory, tmp_path, now, lines=late))[1]["news.morning_brief"] == \
        "the morning brief was written at 09:40, after this read"
    unsettled = brief_scene(scene_factory, tmp_path, at(9, 34, BRIEF_DAY, ss=30))
    assert labels(unsettled)[1]["news.morning_brief"] == "the settled open (the close of the 09:34 bar) has not finished yet"


# ---- shock.burst, shock.vs_day_range and the shock gate

BURST_DAY = "2026-10-13"


def burst_scene(scene_factory, now, *, size=12.0, after=12.0, start=(11, 40), prior_rulers=NORMAL, **kw):
    """A tape stepping half a point a minute, rising ``size`` points in the five minutes from ``start``, then held at
    ``after`` points over where it started from 11:50."""
    i0 = minute(*start)
    closes = []
    for i in range(minute(15, 59) + 1):
        lift = 0.0 if i < i0 else size * min(i - i0 + 1, 5) / 5 if i < minute(11, 50) else after
        closes.append(7700.0 + 0.5 * (i % 2) + lift)
    return scene_at(scene_factory, now, closes, prior_rulers=prior_rulers, **kw)


def test_a_burst_the_hour_before_could_not_produce(scene_factory):
    state, _, gates = labels(burst_scene(scene_factory, at(12, 2, BURST_DAY), after=5.5))
    assert state["shock.burst"] == (
        "22 minutes ago price rose 0.12 sigma in 5 minutes, 8.2 times what the hour before's minute-to-minute movement would "
        "produce, past the 3.0-times shock rule and the 0.10 sigma floor; one minute inside it was 5 times a normal minute; "
        "not at a scheduled release time; the burst ended 17 minutes ago, older than the 10-minute fresh window; since then "
        "price has given back 52% of it, past the half line")
    assert state["shock.vs_day_range"] == (
        "the shock took price to a new session high, 0.12 sigma over the earlier high, which price has since pushed 0.01 sigma "
        "further, and price is 0.07 sigma below that new high, within the 0.09 sigma move rule of it")
    assert gates["shock_state"] is None


def test_the_shock_rule_floor_and_fresh_window_at_their_boundaries(scene_factory):
    fresh = labels(burst_scene(scene_factory, at(11, 55, BURST_DAY)))[0]["shock.burst"]
    assert "the burst ended 10 minutes ago, inside the 10-minute fresh window; since then price has given back none of it" in fresh
    assert "ended 11 minutes ago, older than" in labels(burst_scene(scene_factory, at(11, 56, BURST_DAY)))[0]["shock.burst"]
    _, omitted, gates = labels(burst_scene(scene_factory, at(12, 2, BURST_DAY), size=9.9, after=9.9))
    assert gates["shock_state"] == omitted["shock.burst"] == (
        "no five-minute move in the last 60 minutes passed the shock rule; the largest was 0.09 sigma, 6.7 times the hour "
        "before's movement")
    _, omitted, _ = labels(burst_scene(scene_factory, at(12, 2, BURST_DAY), size=3.5, after=3.5))
    assert omitted["shock.burst"].endswith("the largest was 0.03 sigma, 2.4 times the hour before's movement")
    assert omitted["shock.vs_day_range"] == omitted["shock.cross_asset"] == omitted["shock.burst"]


def test_a_burst_older_than_the_lookback_no_longer_counts(scene_factory):
    s = burst_scene(scene_factory, at(12, 44, BURST_DAY))                  # the burst ended at 11:45
    assert labels(s)[0]["shock.burst"].startswith("64 minutes ago")
    assert labels(replace(s, now=at(12, 45, BURST_DAY)))[2]["shock_state"].startswith("no five-minute move in the last 60 minutes")


def test_a_burst_finishing_after_the_read_never_counts(scene_factory):
    s = burst_scene(scene_factory, at(11, 44, BURST_DAY, ss=30))          # the burst's last bar finishes at 11:45
    assert all(float(b["close"]) < 7712.0 for b in s.bars)
    assert "0.12 sigma" not in labels(s)[0].get("shock.burst", "")


def test_an_opening_burst_is_ranked_against_the_same_five_minutes(scene_factory):
    prior = prior_sessions(8)
    rulers = {**NORMAL, **{d: SigmaRuler(100.0, "anchor") for d in prior}}
    s = burst_scene(scene_factory, at(10, 8, BURST_DAY), size=30.0, after=30.0, start=(9, 56), prior_bars=prior, prior_rulers=rulers)
    assert labels(s)[0]["shock.burst"] == (
        "12 minutes ago price rose 0.29 sigma in 5 minutes, higher than 8 of the last 8 sessions over the same five minutes, "
        "past the 95% opening-burst line and the 0.10 sigma floor; not at a scheduled release time; the burst ended 7 minutes "
        "ago, inside the 10-minute fresh window; since then price has given back none of it, within the half line")
    usual = burst_scene(scene_factory, at(10, 8, BURST_DAY), size=14.0, after=14.0, start=(9, 56), prior_bars=prior, prior_rulers=rulers)
    assert labels(usual)[2]["shock_state"] == ("no five-minute move in the last 60 minutes passed the shock rule; the largest was "
                                               "0.14 sigma")                # the prior sessions moved 0.15 over those minutes
    early = burst_scene(scene_factory, at(9, 38, BURST_DAY), prior_bars=prior, prior_rulers=rulers)
    assert labels(early)[1]["shock.burst"] == "the first five minutes after the settled open end at 09:40"


def test_a_burst_at_a_release_names_it_and_an_estimated_ruler_says_so(scene_factory):
    s = burst_scene(scene_factory, at(10, 32, "2026-10-01"), start=(10, 0), size=30.0, after=30.0, anchor_row=False,
                    prior_bars=prior_sessions(8), prior_rulers={**NORMAL, **{d: SigmaRuler(100.0, "anchor") for d in prior_sessions(8)}})
    text = labels(s)[0]["shock.burst"]
    assert text.startswith("32 minutes ago price rose 0.29 sigma (ruler estimated) in 5 minutes")
    assert "; inside the first 15 minutes after the ISM manufacturing report at 10:00;" in text


def test_the_shock_labels_need_a_ruler(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), anchor_row=False, sigma_live=None)
    _, omitted, gates = labels(s)
    why = "no morning sigma ruler: no row by 09:40, no live sigma and no VIX at the settled open"
    assert omitted["shock.burst"] == omitted["shock.vs_day_range"] == omitted["shock.cross_asset"] == gates["shock_state"] == why


def test_a_burst_inside_the_days_range_and_one_left_behind(scene_factory):
    early_high = [7700.0 + 0.5 * (i % 2) + (20.0 if minute(10, 0) <= i < minute(10, 5) else 0.0) for i in range(minute(15, 59) + 1)]
    for i in range(minute(11, 40), len(early_high)):
        early_high[i] += 12.0 * min(i - minute(11, 40) + 1, 5) / 5
    inside = scene_at(scene_factory, at(12, 2, BURST_DAY), early_high)
    assert labels(inside)[0]["shock.vs_day_range"] == ("the shock stayed inside the day's earlier range: its high stopped 0.09 sigma "
                                                       "short of the earlier session high")
    left = labels(burst_scene(scene_factory, at(12, 2, BURST_DAY), after=2.0))[0]["shock.vs_day_range"]
    assert left.endswith("and price is 0.10 sigma below that new high, beyond the 0.09 sigma move rule of it")


def test_a_burst_price_kept_extending_is_measured_back_from_the_newest_extreme(scene_factory):
    closes = burst_scene(scene_factory, at(12, 2, BURST_DAY)).bars
    climb = [float(b["close"]) + 1.5 * min(max(i - minute(11, 49), 0), 10) for i, b in enumerate(closes)]   # 1.5 a minute to 12:00
    s = scene_at(scene_factory, at(12, 2, BURST_DAY), climb)
    assert labels(s)[0]["shock.vs_day_range"] == (
        "the shock took price to a new session high, 0.12 sigma over the earlier high, which price has since pushed 0.15 sigma "
        "further, and price is 0.01 sigma below that new high, within the 0.09 sigma move rule of it")
    beyond = labels(scene_at(scene_factory, at(12, 2, BURST_DAY), climb, spot=7730.0))[0]["shock.vs_day_range"]
    assert beyond.endswith("and price is at or above that new high, within the 0.09 sigma move rule of it")


def test_the_shocks_range_and_cross_asset_reads_say_when_the_ruler_is_estimated(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0, anchor_row=False)
    state, _, _ = labels(replace(s, market=burst_market(s)))
    assert state["shock.vs_day_range"].endswith("within the 0.09 sigma move rule of it (ruler estimated)")
    assert state["shock.cross_asset"].endswith("short of the 0.10 sigma defensive-bid rule (ruler estimated)")


# ---- shock.cross_asset

SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")


def burst_market(scene, tnx_jump=0.041, smh_extra=0.0, defensive_extra=0.0, lagging=("XLRE",), tick=1240.0, drop=(),
                 burst=(11, 40)):
    """Every fund moving with the index minute by minute from 10:30, the ten-year yield flat, the NYSE TICK at 300;
    during the five-minute burst from ``burst`` the yield jumps, SMH and the defensive funds move ``extra`` index points
    more, and the ``lagging`` sector funds fall."""
    known: dict[str, list] = {}
    start = at(*burst, BURST_DAY)
    end = start + timedelta(minutes=5)
    for b in scene.bars:
        t = datetime.fromisoformat(b["ts"]) + timedelta(minutes=1)
        if t < at(10, 30, BURST_DAY):
            continue
        spx, during = float(b["close"]), start < t <= end
        known.setdefault("$TNX", []).append((t, 5.17 + (tnx_jump if t > start else 0.0)))
        known.setdefault("SMH", []).append((t, (spx + (smh_extra if t > start else 0.0)) / 20.0))
        for x in SECTORS:
            extra = defensive_extra if x in ("XLP", "XLU", "XLV") else -20.0 if x in lagging else 0.0
            known.setdefault(x, []).append((t, (spx + (extra if t > start else 0.0)) / 80.0))
        known.setdefault("$TICK", []).append((t, tick if during else 300.0))
    for symbol in drop:
        known.pop(symbol)
    return MarketContext(known)


def test_what_moved_with_the_shock(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0)
    state, _, _ = labels(replace(s, market=burst_market(s, smh_extra=15.0, defensive_extra=-11.0)))
    assert state["shock.cross_asset"] == (
        "during the shock the ten-year yield rose 4.1 basis points beyond its usual link to the index, past the 3 basis-point rule; "
        "semiconductors rose 0.15 sigma beyond theirs, past the 0.10 sigma rule, the shock's way; no megacap's share of it is "
        "measured, since their index weights are not on file; 10 of 11 sector funds rose with it, at or past the 9-fund broad "
        "count, and NYSE TICK reached 1240, at or past the 1000 extreme; the defensive funds (staples, utilities, health care) "
        "fell 0.11 sigma beyond their usual link, against the shock, not the 0.10 sigma defensive-bid rule, which needs them "
        "rising against a falling index")


def test_defensives_rising_against_a_falling_index_are_the_defensive_bid(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), size=-12.0, after=-6.0)
    text = labels(replace(s, market=burst_market(s, defensive_extra=11.0, tick=-1240.0)))[0]["shock.cross_asset"]
    assert text.endswith("the defensive funds (staples, utilities, health care) rose 0.11 sigma beyond their usual link, "
                         "against the shock, past the 0.10 sigma defensive-bid rule")
    short = labels(replace(s, market=burst_market(s, defensive_extra=9.0, tick=-1240.0)))[0]["shock.cross_asset"]
    assert short.endswith("rose 0.09 sigma beyond their usual link, against the shock, short of the 0.10 sigma defensive-bid rule")
    rally = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0)
    with_it = labels(replace(rally, market=burst_market(rally, defensive_extra=11.0)))[0]["shock.cross_asset"]
    assert with_it.endswith("rose 0.11 sigma beyond their usual link, with the shock, not the 0.10 sigma defensive-bid rule, "
                            "which needs them rising against a falling index")


def test_the_cross_asset_rules_at_their_boundaries(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0)
    market = burst_market(s, tnx_jump=0.029, smh_extra=-15.0, lagging=("XLRE", "XLB", "XLC"), tick=999.0)
    text = labels(replace(s, market=market))[0]["shock.cross_asset"]
    assert "the ten-year yield rose 2.9 basis points beyond its usual link to the index, short of the 3 basis-point rule" in text
    assert "semiconductors fell 0.15 sigma beyond theirs, past the 0.10 sigma rule but against the shock" in text
    assert "8 of 11 sector funds rose with it, short of the 9-fund broad count, and NYSE TICK reached 999, short of the 1000 extreme" in text
    assert text.endswith("moved with their usual link to the index, short of the 0.10 sigma defensive-bid rule")
    at_the_lines = labels(replace(s, market=burst_market(s, lagging=("XLRE", "XLB"), tick=1000.0)))[0]["shock.cross_asset"]
    assert "9 of 11 sector funds rose with it, at or past the 9-fund broad count, and NYSE TICK reached 1000, at or past" in at_the_lines
    still = labels(replace(s, market=burst_market(s, tnx_jump=0.0004, smh_extra=0.4)))[0]["shock.cross_asset"]
    assert still.startswith("during the shock the ten-year yield moved with its usual link to the index, short of the 3 basis-point "
                            "rule; semiconductors moved with their usual link, short of the 0.10 sigma rule;")
    wrong_way = labels(replace(s, market=burst_market(s, tick=-1100.0)))[0]["shock.cross_asset"]
    assert "NYSE TICK reached -1100, past the 1000 extreme but against the shock;" in wrong_way


def test_the_cross_asset_label_is_omitted_without_its_markets(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0)
    assert labels(s)[1]["shock.cross_asset"] == "no market-context snapshot today"
    assert labels(replace(s, market=burst_market(s, drop=("SMH",))))[1]["shock.cross_asset"] == \
        "SMH has no value within 2 minutes of the burst's start and end"
    assert labels(replace(s, market=burst_market(s, drop=("XLK", "XLF", "XLE"))))[1]["shock.cross_asset"] == \
        "needs 9 of the 11 sector funds with a value at the burst's start and end, have 8"
    late = MarketContext({k: [(t, v) for t, v in pts if t > at(11, 20, BURST_DAY)] for k, pts in burst_market(s).known.items()})
    assert labels(replace(s, market=late))[1]["shock.cross_asset"] == \
        "needs 30 minutes of SMH in the hour before the burst to fit its link to the index, has 19"


def test_without_the_ten_year_yield_the_rest_of_the_shock_is_still_read(scene_factory):
    s = burst_scene(scene_factory, at(12, 2, BURST_DAY), after=6.0)
    text = labels(replace(s, market=burst_market(s, drop=("$TNX",))))[0]["shock.cross_asset"]
    assert text.startswith("during the shock the ten-year yield is not measured ($TNX has no value within 2 minutes of the burst's "
                           "start and end); semiconductors ")
    assert "10 of 11 sector funds rose with it, at or past the 9-fund broad count" in text
    late = burst_scene(scene_factory, at(15, 32, BURST_DAY), start=(15, 10))
    stopped = MarketContext({k: [(t, v) for t, v in pts if k != "$TNX" or t <= at(15, 0, BURST_DAY)]    # its bars stop at 15:00
                             for k, pts in burst_market(late, burst=(15, 10)).known.items()})
    text = labels(replace(late, market=stopped))[0]["shock.cross_asset"]
    assert text.startswith("during the shock the ten-year yield is not measured ($TNX has no value within 2 minutes of the burst's "
                           "start and end); semiconductors ")
    assert "NYSE TICK reached 1240, at or past the 1000 extreme" in text
