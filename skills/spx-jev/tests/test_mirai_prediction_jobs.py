"""The table builder's checks, the scoreboard's words, the fits' leak check and the hook's safety, on temp folders."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from spx_jev.mirai_prediction import paths, read_hook, scoreboard, table_builder, voice_fits
from spx_jev.mirai_prediction.answer_matrix import AnswerMatrix, Row
from spx_jev.mirai_prediction.nightly_job import days_to_catch_up

PROBS = {"up": 0.2, "flat": 0.6, "down": 0.2}


def line(read_id="live:2026-10-01T10:00:00-04:00", sum_id="average_30", voice="historical_odds", probs=PROBS, **more) -> dict:
    return {"read_id": read_id, "lane": "live", "day": "2026-10-01", "row_ts": "2026-10-01T10:00:00-04:00", "sum_id": sum_id,
            "voice_name": voice, "voice_probs": probs, "fit_day": "2026-10-01", "created_at": "2026-10-01T10:00:05-04:00",
            "source": "live", **more}


def test_check_refuses_bad_rows_and_keeps_good_ones():
    assert table_builder.check_forecast_row(line(), None) is None
    assert "sum" in table_builder.check_forecast_row(line(probs={"up": 0.5, "flat": 0.5, "down": 0.5}), None)
    assert "map" in table_builder.check_forecast_row(line(probs=[0.2, 0.6, 0.2]), None)
    assert "store" in table_builder.check_forecast_row(line(), {"live:other"})
    assert "before" in table_builder.check_forecast_row(line(created_at="2026-10-01T09:00:00-04:00"), None)
    assert table_builder.check_forecast_row(line(created_at="2026-10-01T09:00:00-04:00", source="replay"), None) is None


def test_table_build_dedupes_and_writes_refused_rows(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(table_builder, "_day_read_ids", lambda *a: None)
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line())
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line(probs={"up": 0.3, "flat": 0.5, "down": 0.2}))
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line(probs={"up": 1, "flat": 1, "down": 1}))
    counts = table_builder.build_new_voice_forecasts_table(root, tmp_path, "live", "2026-10-01")
    assert counts == {"raw_lines": 3, "rows": 1, "refused": 1, "duplicates": 1, "store_day_known": False}
    rows = table_builder.load_day_forecasts(root, "2026-10-01")
    assert rows[0]["voice_probs"]["up"] == pytest.approx(0.3)          # the last line for a key wins
    assert rows[0]["learn_exclude"] is False
    assert not list((paths.forecasts_table_dir(root) / "day=2026-10-01").glob("*.tmp.parquet"))
    assert len(paths.read_json_lines(paths.refused_rows_file(root, "2026-10-01"))) == 1


def test_scores_join_on_read_and_sum(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(table_builder, "_day_read_ids", lambda *a: None)
    for sid in ("average_30", "next_60"):
        for v in ("historical_odds", "pool_v2"):
            paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line(sum_id=sid, voice=v))
    table_builder.build_new_voice_forecasts_table(root, tmp_path, "live", "2026-10-01")
    monkeypatch.setattr(table_builder, "load_day_outcomes", lambda *a: {("live:2026-10-01T10:00:00-04:00", "average_30"): "flat"})
    counts = table_builder.build_voice_scores_table(root, tmp_path, "live", "2026-10-01")
    assert counts["scored"] == 2                                        # only the graded sum, both its voices
    scored = scoreboard.load_all_scores(root)
    assert {r["sum_id"] for r in scored} == {"average_30"} and all(r["right"] for r in scored)
    assert all(r["reference_penalty"] == pytest.approx(r["penalty"]) for r in scored)


def test_an_excluded_read_is_scored_but_not_learned(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(table_builder, "_day_read_ids", lambda *a: None)
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line(learn_exclude=True))
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"),
                           line(read_id="live:other", row_ts="2026-10-01T11:00:00-04:00", created_at="2026-10-01T11:00:05-04:00"))
    table_builder.build_new_voice_forecasts_table(root, tmp_path, "live", "2026-10-01")
    assert set(table_builder.day_forecasts_by_read(root, "2026-10-01", "average_30")) == {"live:other"}
    assert len(table_builder.day_forecasts_by_read(root, "2026-10-01", "average_30", skip_excluded=False)) == 2


def test_status_words():
    assert scoreboard.status_word(50, [1.0, 2.0]) == "Too early"
    assert scoreboard.status_word(150, [-1.0, 2.0]) == "Too early"
    assert scoreboard.status_word(350, [-1.0, 2.0]) == "No edge"
    assert scoreboard.status_word(350, [0.5, 2.0]) == "Edge"
    assert scoreboard.status_word(350, [-3.0, -0.5]) == "Worse"


def test_skill_is_one_minus_the_penalty_ratio():
    rows = [{"penalty": 0.5, "reference_penalty": 1.0, "day": "d1", "right": True}, {"penalty": 0.5, "reference_penalty": 1.0, "day": "d2", "right": False}]
    assert scoreboard.skill_pct(rows) == pytest.approx(50.0)
    assert scoreboard.skill_pct([]) is None


def test_fit_for_a_day_refuses_rows_from_that_day():
    row = Row(read_id="r", row_ts="2026-10-01T10:00:00", day="2026-10-01", sum_id="average_30", historical_odds_probs=PROBS,
              jev_own_probs=None, shown_probs=None, pool_v1_probs=None, outcome="flat", learn_exclude=False, answers={})
    m = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-01", rows=[row], columns={})
    with pytest.raises(voice_fits.LeakError):
        voice_fits.check_no_leak(m, "2026-10-01")
    voice_fits.check_no_leak(m, "2026-10-02")


def test_load_fits_never_returns_a_later_days_fit(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    matrix = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-02", rows=[], columns={})
    doc = {"version": voice_fits.FIT_VERSION, "fit_day": "2026-10-02", "max_day_used": "2026-10-01",
           "additive_scorer": {"pushes": {}, "answer_normal_odds": {}, "layer_volume": {}, "columns": {}},
           "matcher": {"group_of_column": {}, "group_weights": {}}, "jev_corrected": {"asleep": True}, "answer_matrix": matrix.to_json()}
    paths.write_json_atomically(paths.voice_fits_file(root, "2026-10-02", "average_30"), doc)
    assert voice_fits.load_voice_fits(root, "average_30", "2026-10-01") is None            # only a later fit exists
    day_fits = voice_fits.load_voice_fits(root, "average_30", "2026-10-03")                # the newest earlier day's
    assert day_fits.fit_day == "2026-10-02" and day_fits.fits["jev_corrected"].asleep and day_fits.matrix.rows == []


def test_an_old_version_fit_is_passed_over_for_the_newest_current_one(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    matrix = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-02", rows=[], columns={})
    doc = {"version": voice_fits.FIT_VERSION, "fit_day": "2026-10-02", "max_day_used": "2026-10-01",
           "additive_scorer": {"pushes": {}, "answer_normal_odds": {}, "layer_volume": {}, "columns": {}},
           "matcher": {"group_of_column": {}, "group_weights": {}}, "jev_corrected": {"asleep": True}, "answer_matrix": matrix.to_json()}
    paths.write_json_atomically(paths.voice_fits_file(root, "2026-10-02", "average_30"), doc)
    paths.write_json_atomically(paths.voice_fits_file(root, "2026-10-05", "average_30"), {**doc, "version": voice_fits.FIT_VERSION - 1, "fit_day": "2026-10-05"})
    assert voice_fits.load_voice_fits(root, "average_30", "2026-10-05").fit_day == "2026-10-02"
    assert voice_fits.load_voice_fits(root, "average_30", "2026-10-05", allow_earlier=False) is None


def test_old_fit_files_are_pruned_and_the_newest_days_kept(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    for d in range(1, 15):
        paths.write_json_atomically(paths.voice_fits_file(root, f"2026-09-{d:02d}", "average_30"), {})
    deleted = paths.prune_voice_fits(root, keep_days=3)
    assert len(deleted) == 11 and sorted(p.name[:10] for p in (root / "catalogs" / "voice_fits").glob("*.json")) == ["2026-09-12", "2026-09-13", "2026-09-14"]


def test_the_archive_keeps_the_newest_backups_per_file_and_a_weeks_forced_replays(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    archive = root / "archive"
    for d in range(1, 13):
        for name in ("weights_average_30.json", "2026-10-01.jsonl"):
            (archive / f"{name}.202610{d:02d}T060000Z").write_text("{}")
    (archive / "weights_next_30.json.20261001T060000Z").write_text("{}")          # one backup: kept
    (archive / "notes.txt").write_text("")                                        # no stamp: left alone
    for d in (1, 5, 6):
        (archive / f"replay_force_202610{d:02d}T060000Z").mkdir()
        (archive / f"replay_force_202610{d:02d}T060000Z" / "pool_v2").mkdir()
    deleted = paths.prune_archive(root, now=datetime(2026, 10, 12, 5, tzinfo=timezone.utc), keep=10, replay_days=7)
    assert sorted(deleted) == sorted([f"{n}.202610{d:02d}T060000Z" for n in ("weights_average_30.json", "2026-10-01.jsonl") for d in (1, 2)]
                                     + ["replay_force_20261001T060000Z"])
    left = sorted(p.name for p in archive.iterdir())
    assert len([n for n in left if n.startswith("weights_average_30.json.")]) == 10 and "notes.txt" in left
    assert "weights_next_30.json.20261001T060000Z" in left and "replay_force_20261005T060000Z" in left
    assert paths.prune_archive(root, now=datetime(2026, 10, 12, 5, tzinfo=timezone.utc)) == []


def test_the_nightly_job_catches_up_the_days_pool_v2_has_not_learned(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    for day in ("2026-09-20", "2026-10-01", "2026-10-02"):
        paths.append_json_line(paths.raw_forecasts_file(root, day), line(day=day))
    assert days_to_catch_up(root, "live", "2026-10-05") == ["2026-10-01", "2026-10-02"]        # 09-20 is past the window


def test_next_market_day_skips_the_weekend():
    assert voice_fits.next_market_day("2026-10-02") == "2026-10-05"
    assert voice_fits.next_market_day("2026-10-05") == "2026-10-06"


def test_the_hook_never_raises_and_logs_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(read_hook, "data_root", lambda s: tmp_path / "spx_jev" / "mirai_prediction")

    def boom(*a, **k):
        raise RuntimeError("no store here")
    monkeypatch.setattr(read_hook, "forecast_read", boom)
    result = read_hook.after_read(tmp_path, "live", "live:2026-10-01T10:00:00-04:00")
    assert result["written"] == 0 and "RuntimeError" in result["error"]
    log = paths.read_json_lines(paths.job_run_log_file(tmp_path / "spx_jev" / "mirai_prediction"))
    assert log and log[-1]["ok"] is False and log[-1]["job_name"] == "read_hook"


def test_the_hook_skips_a_read_already_written(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    paths.append_json_line(paths.raw_forecasts_file(root, "2026-10-01"), line())
    assert read_hook.already_written(root, "2026-10-01", "live:2026-10-01T10:00:00-04:00", "average_30")
    assert not read_hook.already_written(root, "2026-10-01", "live:2026-10-01T10:00:00-04:00", "next_60")


def test_atomic_json_and_backups(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    target = root / "pools" / "pool_v2" / "weights_x.json"
    paths.write_json_atomically(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1} and not target.with_name(target.name + ".tmp").exists()
    copy = paths.backup_before_change(root, target)
    assert copy and copy.exists() and copy.parent == root / "archive"


def test_pool_2_takes_over_the_call_and_keeps_the_blend_beside_it():
    from spx_jev.lane import LIVE
    from spx_jev.mirai_prediction import live_call
    blend = {"up": 0.3, "flat": 0.4, "down": 0.3}
    hour = {"primary": "next_30", "shown_source": "blend50_exact",
            "by": {"next_30": {"pick": "flat", "probabilities": blend}, "next_60": {"pick": "flat", "probabilities": blend}},
            "pick": "flat", "probabilities": blend,
            "average": {"pick": "flat", "probabilities": blend, "shown_source": "blend50_exact", "by": {"average_30": {"pick": "flat", "probabilities": blend}}}}
    pool_2 = {"average_30": {"up": 0.6, "flat": 0.2, "down": 0.2}, "next_60": {"up": 0.1, "flat": 0.2, "down": 0.7}}
    out = live_call.take_over(hour, pool_2, LIVE)
    avg = out["average"]
    assert avg["pick"] == "up" and avg["probabilities"] == pool_2["average_30"] and avg["blend50_exact"] == blend
    assert avg["shown_source"] == "pool_v2" and avg["by"]["average_30"]["pick"] == "up"
    assert out["by"]["next_60"]["pick"] == "down" and out["by"]["next_60"]["blend50_exact"] == blend
    assert out["by"]["next_30"]["pick"] == "flat" and "blend50_exact" not in out["by"]["next_30"]   # not learned: untouched
    assert out["pick"] == "flat" and out["shown_source"] == "pool_v2"                               # the primary (next_30) kept its blend
    assert hour["average"]["pick"] == "flat"                                                        # the input is never changed in place


def test_pool_2_leaves_a_sum_it_did_not_forecast_alone():
    from spx_jev.lane import LIVE
    from spx_jev.mirai_prediction import live_call
    hour = {"by": {}, "average": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.4, "down": 0.3}}}
    assert live_call.take_over(hour, {}, LIVE) == hour


def _pool_2_hour():
    blend = {"up": 0.3, "flat": 0.4, "down": 0.3}
    return {"primary": "next_30", "shown_source": "blend50_exact",
            "by": {"next_30": {"pick": "flat", "probabilities": blend}, "next_60": {"pick": "flat", "probabilities": blend}},
            "pick": "flat", "probabilities": blend,
            "average": {"pick": "flat", "probabilities": blend, "shown_source": "blend50_exact", "by": {"average_30": {"pick": "flat", "probabilities": blend}}}}


def _mixed(**shares):
    return {"probabilities": {v: {"up": 0.2, "flat": 0.5, "down": 0.3} for v in shares}, "shares": shares}


def test_pool_2_takes_over_with_the_voices_that_went_into_each_sum_most_say_first():
    """The phone lists what went into the combined forecast: each mixed voice's own odds and its share of the say, on the
    call (the average-price sum) and on the 60-minute sum, ordered by the share; the 30-minute sum Pool 2 never forecast
    carries none."""
    from spx_jev.lane import LIVE
    from spx_jev.mirai_prediction import live_call
    pool_2 = {"average_30": {"up": 0.6, "flat": 0.2, "down": 0.2}, "next_60": {"up": 0.1, "flat": 0.2, "down": 0.7}}
    voices = {"average_30": _mixed(historical_odds=0.2, matcher=0.5, jev_own=0.3), "next_60": _mixed(blend_50_50=0.1, additive_scorer=0.9)}
    out = live_call.take_over(_pool_2_hour(), pool_2, LIVE, voices)
    avg = out["average"]["voices"]
    assert [v["name"] for v in avg] == ["matcher", "jev_own", "historical_odds"] and [v["share"] for v in avg] == [0.5, 0.3, 0.2]
    assert avg[0] == {"name": "matcher", "plain_name": "Matcher", "probabilities": {"up": 0.2, "flat": 0.5, "down": 0.3}, "share": 0.5}
    assert [v["name"] for v in out["by"]["next_60"]["voices"]] == ["additive_scorer", "blend_50_50"]
    assert out["by"]["next_60"]["shown_source"] == "pool_v2" and "voices" not in out["by"]["next_30"]
    assert "voices" not in out and out["average"]["pick"] == "up"


def test_no_forecast_means_no_voices_and_a_voice_list_that_cannot_be_read_never_costs_the_call():
    from spx_jev.lane import LIVE
    from spx_jev.mirai_prediction import live_call
    voices = {"average_30": _mixed(matcher=1.0), "next_60": _mixed(matcher=1.0)}
    assert live_call.take_over(_pool_2_hour(), {}, LIVE, voices) == _pool_2_hour()
    pool_2 = {"average_30": {"up": 0.6, "flat": 0.2, "down": 0.2}}
    out = live_call.take_over(_pool_2_hour(), pool_2, LIVE, {"average_30": {"probabilities": {"matcher": {}}}})   # no shares
    assert out["average"]["pick"] == "up" and out["average"]["shown_source"] == "pool_v2" and "voices" not in out["average"]
    assert "voices" not in live_call.take_over(_pool_2_hour(), pool_2, LIVE)["average"]


def test_the_voices_mixed_are_the_ones_pool_2_weighs_never_the_pools_or_the_benched_with_shares_summing_to_one():
    from spx_jev.mirai_prediction.pool_v2 import new_pool_v2
    state = new_pool_v2("average_30", ["historical_odds", "matcher", "jev_own"])
    forecasts = {v: PROBS for v in ("historical_odds", "matcher", "jev_own", "pool_v1", "not_in_the_pool")}
    got = read_hook.mixed_voices(state, forecasts)
    assert set(got["probabilities"]) == set(got["shares"]) == {"historical_odds", "matcher"}   # jev_own benched (SPX30-1)
    assert abs(sum(got["shares"].values()) - 1.0) < 1e-9
    assert read_hook.mixed_voices(state, {"pool_v1": PROBS}) == {"probabilities": {}, "shares": {}}


def test_the_live_forecast_returns_each_sums_mixed_voices_beside_its_mix_and_the_log_keeps_neither(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from spx_jev.mirai_prediction import live_call
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(read_hook, "data_root", lambda s: root)
    monkeypatch.setattr(read_hook, "code_features_for_read", lambda *a: {})
    empty = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-01", rows=[], columns={})
    monkeypatch.setattr(read_hook, "day_fits_for", lambda *a: voice_fits.DayFits(fits={}, matrix=empty, fit_day=None))
    row = SimpleNamespace(read_id="live:2026-10-01T10:00:00-04:00", row_ts="2026-10-01T10:00:00-04:00", learn_exclude=False)
    monkeypatch.setattr(read_hook, "todays_rows", lambda *a: {"average_30": row})
    forecasts = {"historical_odds": PROBS, "matcher": {"up": 0.6, "flat": 0.2, "down": 0.2}, "pool_v1": PROBS}
    monkeypatch.setattr(read_hook, "all_voice_forecasts", lambda *a: (forecasts, {}))
    rec = {"read_id": row.read_id}
    mixed, voices = live_call.forecast_now_with_voices(tmp_path, "live", "2026-10-01", rec, {"row_ts": row.row_ts})
    assert set(mixed) == set(voices) == {"average_30"}
    assert set(voices["average_30"]["probabilities"]) == set(voices["average_30"]["shares"]) == {"historical_odds", "matcher"}
    assert live_call.forecast_now_with_voices(tmp_path, "live", "2026-10-02", rec, {"row_ts": row.row_ts})[0] == mixed
    monkeypatch.setattr(read_hook, "load_archive_read", lambda *a: rec)
    read_hook.after_read(tmp_path, "live", row.read_id, "2026-10-03")
    logged = paths.read_json_lines(paths.job_run_log_file(root))[-1]
    assert logged["ok"] is True and "voices" not in logged and "pool_v2" not in logged


def test_a_voices_list_that_fails_drops_only_that_list_and_the_sum_keeps_its_mix(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from spx_jev.mirai_prediction import live_call
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(read_hook, "data_root", lambda s: root)
    monkeypatch.setattr(read_hook, "code_features_for_read", lambda *a: {})
    empty = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-01", rows=[], columns={})
    monkeypatch.setattr(read_hook, "day_fits_for", lambda *a: voice_fits.DayFits(fits={}, matrix=empty, fit_day=None))
    row = SimpleNamespace(read_id="live:2026-10-01T10:00:00-04:00", row_ts="2026-10-01T10:00:00-04:00", learn_exclude=False)
    monkeypatch.setattr(read_hook, "todays_rows", lambda *a: {"average_30": row})
    monkeypatch.setattr(read_hook, "all_voice_forecasts", lambda *a: ({"historical_odds": PROBS, "jev_own": PROBS}, {}))

    def broken(*a):
        raise KeyError("move")
    monkeypatch.setattr(read_hook, "mixed_voices", broken)
    mixed, voices = live_call.forecast_now_with_voices(tmp_path, "live", "2026-10-01", {"read_id": row.read_id}, {"row_ts": row.row_ts})
    assert set(mixed) == {"average_30"} and voices == {}
    logged = paths.read_json_lines(paths.job_run_log_file(root))[-1]
    assert logged["job_name"] == "mixed_voices" and logged["ok"] is False and logged["sum_id"] == "average_30"


def test_prune_archive_on_a_missing_or_empty_folder_deletes_nothing(tmp_path):
    from datetime import datetime, timezone
    root = tmp_path / "spx_jev" / "mirai_prediction"
    assert paths.prune_archive(root, now=datetime(2026, 10, 7, tzinfo=timezone.utc)) == []        # no archive folder yet
    (root / "archive").mkdir(parents=True)
    assert paths.prune_archive(root, now=datetime(2026, 10, 7, tzinfo=timezone.utc)) == []        # an empty one


def test_a_forced_replay_folder_without_a_readable_stamp_is_pruned_by_its_age(tmp_path):
    import os, time
    from datetime import datetime, timezone
    root = tmp_path / "spx_jev" / "mirai_prediction"
    old = root / "archive" / "replay_force_odd-name"
    old.mkdir(parents=True)
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    assert paths.prune_archive(root, now=datetime.now(timezone.utc)) == ["replay_force_odd-name"]
