"""One read, end to end: freeze the payload, ask Claude twice, check the answers, write the final forecast.

    python -m spx_claude_forecast.read_runner --state-dir <state> --read-id live:<row_ts>

Started detached by ``hook.spawn_claude_forecast`` after the SPX read has archived its record, so nothing
here is ever on the read's path. Every step lands a line before the next starts: the payload line
(``payloads/{day}.jsonl``) before any call; each answer line (``reads/{day}.jsonl``) as it comes back; the
final forecast line last; then ``latest.json`` for the phone. A read that cannot be built writes a
``blind`` line; a paused switch writes the payload and a ``paused`` final line; the daily cap writes
``capped``. The run never raises past ``main``.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import answer_checks, claude_call, control, final_forecast, jsonl_store, library, precedents, rulebook
from .control import log, log_job_run, now_utc_iso
from .paths import ForecastPaths, default_state_dir, ensure_folders
from .payload.build import build_payload
from .payload.frozen_inputs import load_frozen_inputs, parse_read_id, slot_of

ANSWERS_PER_READ = ("answer_1", "answer_2")
TEST_VARIANT_NONE = "none"


def run_read(state_dir: Path | str, read_id: str, *, call=claude_call.call_claude) -> dict:
    """Run one read; returns a short summary. ``call`` is the Claude caller or a stand-in for tests."""
    started = now_utc_iso()
    paths = ensure_folders(state_dir)
    lane, row_ts = parse_read_id(read_id)
    day = row_ts[:10]
    summary: dict = {"read_id": read_id, "day": day}
    off = control.switched_off_reason()
    if off:
        summary["skipped"] = off
        return summary
    if _already_written(paths, day, read_id):
        summary["skipped"] = "already written"
        return summary
    try:
        inputs = load_frozen_inputs(state_dir, paths, read_id)
    except Exception as e:
        line = _blind_line(read_id, day, row_ts, f"{type(e).__name__}: {e}")
        jsonl_store.append_json_line(paths.payloads_file(day), line)
        log_job_run(paths, "read:blind", started, False, error=line["why"], read_id=read_id)
        summary["status"] = "blind"
        summary["why"] = line["why"]
        return summary

    rows = library.read_library(paths)
    built = build_payload(inputs, rows, origin="live", library_manifest=library.read_manifest(paths))
    built.line["call"] = {"model": claude_call.PINNED_MODEL, "effort": claude_call.CALL_EFFORT}
    jsonl_store.append_json_line(paths.payloads_file(day), built.line)
    summary["status"] = built.line["status"]
    summary["claude_input_sha256"] = built.claude_input_sha256
    if built.refused_because:
        log_job_run(paths, "read:refused", started, False, error="; ".join(built.refused_because), read_id=read_id)
        return summary

    hold = _why_not_to_call(paths, day)
    if hold:
        final = _final_line(read_id, built, [], hold, inputs)
        jsonl_store.append_json_line(paths.reads_file(day), final)
        _write_latest(paths, built, final, inputs)
        log_job_run(paths, f"read:{hold}", started, True, read_id=read_id)
        summary["status"] = hold
        return summary

    rules = rulebook.rulebook_text()
    jsonl_store.write_bytes_once(paths.rules_file(rulebook.rulebook_sha()), rules.encode("utf-8"))
    version = claude_call.cli_version()
    with ThreadPoolExecutor(max_workers=len(ANSWERS_PER_READ)) as pool:
        futures = {name: pool.submit(call, built.prompts[name], rules) for name in ANSWERS_PER_READ}
        answers = []
        for name in ANSWERS_PER_READ:
            result = futures[name].result()
            line = _answer_line(read_id, name, built, result, version)
            jsonl_store.append_json_line(paths.reads_file(day), line)
            answers.append(line)
    final = _final_line(read_id, built, answers, "ok", inputs)
    jsonl_store.append_json_line(paths.reads_file(day), final)
    _write_latest(paths, built, final, inputs)
    errors = [a["error"] for a in answers if a.get("error")]
    log_job_run(paths, "read", started, not errors, error="; ".join(errors) or None, read_id=read_id,
                usable_answers=final["usable_answer_count"],
                cost_usd=round(sum((a.get("call_stats") or {}).get("cost_usd", 0) for a in answers), 4))
    summary["usable_answers"] = final["usable_answer_count"]
    summary["errors"] = errors
    return summary


# --- gates ----------------------------------------------------------------------------------------------------------

def _already_written(paths: ForecastPaths, day: str, read_id: str) -> bool:
    return any(line.get("read_id") == read_id for line in jsonl_store.iter_json_lines(paths.payloads_file(day)))


def _why_not_to_call(paths: ForecastPaths, day: str) -> str | None:
    """``paused`` or ``capped`` when the call must not be made, else None."""
    if control.is_paused(paths):
        return "paused"
    limits = control.load_control(paths)
    calls_today = sum(1 for line in jsonl_store.iter_json_lines(paths.reads_file(day))
                      if line.get("line_type") == "claude_answer" and line.get("call_stats"))
    if calls_today >= int(limits.get("max_calls_per_day", 0) or 0):
        return "capped"
    spent = sum((line.get("call_stats") or {}).get("cost_usd", 0) for line in jsonl_store.iter_json_lines(paths.reads_file(day))
                if line.get("line_type") == "claude_answer")
    if spent >= float(limits.get("max_usd_per_day", 0) or 0):
        return "capped"
    return None


# --- the lines -------------------------------------------------------------------------------------------------------

def _blind_line(read_id: str, day: str, row_ts: str, why: str) -> dict:
    from .payload.frozen_inputs import ET
    from datetime import datetime
    cut = datetime.fromisoformat(row_ts).astimezone(ET)
    return {"line_type": "payload", "read_id": read_id, "prompt_and_model_version": rulebook.PROMPT_AND_MODEL_VERSION,
            "trading_day": day, "half_hour_slot_et": slot_of(cut), "origin": "live", "status": "blind", "why": why,
            "cut_at": cut.isoformat(), "built_at": now_utc_iso()}


def _answer_line(read_id: str, name: str, built, result: claude_call.CallResult, cli_version: str | None) -> dict:
    checked = answer_checks.check_answer(result.answer_parsed, built.scene, built.asked_horizons, built.card_ids)
    return {"line_type": "claude_answer", "answer_id": f"{read_id}#{name}", "read_id": read_id,
            "claude_input_sha256": built.claude_input_sha256, "prompt_and_model_version": rulebook.PROMPT_AND_MODEL_VERSION,
            "test_variant": TEST_VARIANT_NONE, "direction_order_asked": built.direction_order[name],
            "precedent_order_shown": built.precedent_order[name], "prompt_sha256": built.prompt_sha256[name],
            "answer_text": result.answer_text, "answer_parsed": result.answer_parsed,
            "answer_checks": checked.answer_checks, "checked_forecast": checked.checked_forecast,
            "checked_similar_precedents": checked.checked_similar_precedents, "checked_reasons": checked.checked_reasons,
            "checked_strongest_reason_against_lean": checked.checked_strongest_reason_against_lean,
            "call_stats": result.call_stats, "cli_version": cli_version, "model_served": result.model_served,
            "error": result.error, "written_at": now_utc_iso()}


def _final_line(read_id: str, built, answers: list[dict], status: str, inputs) -> dict:
    last_30 = ((built.scene.get("tape") or {}).get("m30_sig") or {}).get("v") if isinstance(built.scene.get("tape"), dict) else None
    final = final_forecast.final_forecast_for(answers, built.asked_horizons, built.base_rate_pct, last_30)
    return {"line_type": "final_forecast", "read_id": read_id, "claude_input_sha256": built.claude_input_sha256,
            "prompt_and_model_version": rulebook.PROMPT_AND_MODEL_VERSION, "test_variant": TEST_VARIANT_NONE,
            "status": status, "trading_day": inputs.day, "half_hour_slot_et": inputs.slot, **final,
            "picked_precedents_among_code_nearest_3_count": final_forecast.picked_precedents_among_code_nearest(answers, built.nearest_card_ids),
            "shown_precedents_outcomes": {h: precedents.shown_precedents_outcomes(
                built.scene["precedents"]["cards"], (built.scene.get("base_rate") or {}).get(h), h)
                for h in built.asked_horizons},
            "written_at": now_utc_iso()}


def _write_latest(paths: ForecastPaths, built, final: dict, inputs) -> None:
    latest = {"read_id": final["read_id"], "trading_day": inputs.day, "half_hour_slot_et": inputs.slot, "status": final["status"],
              "forecast": final.get("forecast"), "usable_answer_count": final.get("usable_answer_count"),
              "claude_input_sha256": built.claude_input_sha256, "written_at": final["written_at"]}
    jsonl_store.write_json_atomically(paths.latest_file, latest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forecast one SPX read with Claude (detached from the read).")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--read-id", required=True)
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()
    try:
        summary = run_read(state_dir, args.read_id)
    except Exception as e:                                           # the runner must never die without a trace
        log(f"read runner failed: {type(e).__name__}: {e}")
        try:
            log_job_run(ensure_folders(state_dir), "read:crashed", now_utc_iso(), False, error=f"{type(e).__name__}: {e}",
                        read_id=args.read_id)
        except Exception:
            pass
        return 1
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
