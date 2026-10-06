"""The table builder's checks, the scoreboard's words, the fits' leak check and the hook's safety, on temp folders."""
from __future__ import annotations

import json
from pathlib import Path

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
    m = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-01", rows=[row], columns={}, level_history={})
    with pytest.raises(voice_fits.LeakError):
        voice_fits.check_no_leak(m, "2026-10-01")
    voice_fits.check_no_leak(m, "2026-10-02")


def test_load_fits_never_returns_a_later_days_fit(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    matrix = AnswerMatrix(lane="live", sum_id="average_30", built_for_day="2026-10-02", rows=[], columns={}, level_history={})
    doc = {"version": voice_fits.FIT_VERSION, "fit_day": "2026-10-02", "max_day_used": "2026-10-01",
           "additive_scorer": {"pushes": {}, "answer_normal_odds": {}, "layer_volume": {}, "columns": {}},
           "matcher": {"group_of_column": {}, "group_weights": {}}, "jev_corrected": {"asleep": True}, "answer_matrix": matrix.to_json()}
    paths.write_json_atomically(paths.voice_fits_file(root, "2026-10-02", "average_30"), doc)
    assert voice_fits.load_voice_fits(root, "average_30", "2026-10-01") is None            # only a later fit exists
    day_fits = voice_fits.load_voice_fits(root, "average_30", "2026-10-03")                # the newest earlier day's
    assert day_fits.fit_day == "2026-10-02" and day_fits.fits["jev_corrected"].asleep and day_fits.matrix.rows == []


def test_old_fit_files_are_pruned_and_the_newest_days_kept(tmp_path):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    for d in range(1, 15):
        paths.write_json_atomically(paths.voice_fits_file(root, f"2026-09-{d:02d}", "average_30"), {})
    deleted = paths.prune_voice_fits(root, keep_days=3)
    assert len(deleted) == 11 and sorted(p.name[:10] for p in (root / "catalogs" / "voice_fits").glob("*.json")) == ["2026-09-12", "2026-09-13", "2026-09-14"]


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
