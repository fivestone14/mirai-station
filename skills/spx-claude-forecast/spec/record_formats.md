# Record formats: reads, outcomes and Claude's answer

**Status: LOCKED 10-09.** Will approved the four changes below. A fresh blind reader then explained every field: 0 lost and 9 guesses, and all 9 were renamed (weight_pct, strongest_reason_against, removed_items, has_valid_reason, similar_precedents_in_nearest_3_count, is_leaning_same_way_as_last_30_minutes_move, existing_system_time_of_day_odds, base_rate_counted_as_precedent_count). Hashes are written as `sha256:` + the first 16 hex characters.

The goal is that someone who knows nothing about this system can read one line and say what every field means. Eight agents produced these names (an inventory, three naming lenses, a merge, two blind readers who had never seen the system, and a final pass), and every name a blind reader marked "guess" or "lost" was fixed.

## Naming rules

- Words: snake_case, whole English words. The only short forms allowed are id, pct, usd, sha256, et and vix. read_id stays exactly as it is, because it joins the station's archive and grades.
- Units: the unit is the last word of every number's name. _pct is a chance from 0 to 100 (everywhere, scores included). _pct_points is the gap between two percents. _points is an S&P 500 index move or distance; a price level has price in its name instead. _flat_edges is a move divided by flat_edge_points. The others are _minutes, _seconds, _usd, _tokens and _count. An ordinal ends in _number. A key with the number 1 uses the singular (flat_within_1_flat_edge).
- Scores keep their standard names, and lower is better for all three: log_loss (in nats, with a 2% floor), brier_score and size_ranked_probability_score.
- Yes/no fields start with is_ or has_. When a flag depends on a threshold, the threshold goes in its name (is_within_3_pct_points_of_base_rate).
- Endings: _id for assigned ids, _version for versions, _at for ISO timestamps and _sha256 for fingerprints, even a fingerprint used as a join key (claude_input_sha256). Every _at is written in Eastern time with its offset, even when it was copied from a UTC source.
- One name per idea across the reply, reads, outcomes and scores. The horizon is always `horizon`, with values next_30_minutes, next_60_minutes and to_close; a reason may also use all. Chances are always up_pct, flat_pct and down_pct as named keys, never a list. The actual result is always direction plus size_bucket. The price a move is measured to is always a field ending in _measured_to, with values window_average_price, window_end_price, official_close_price or last_bar_close_price.
- The seven size buckets are always named keys or values, never positions in a list: down_over_3_flat_edges, down_2_to_3_flat_edges, down_1_to_2_flat_edges, flat_within_1_flat_edge, up_1_to_2_flat_edges, up_2_to_3_flat_edges, up_over_3_flat_edges. up_by_size_pct and down_by_size_pct use the same keys.
- Every forecaster uses one forecast block: {status, up_pct, flat_pct, down_pct, size_buckets_pct}. A comparison forecast carries the three chances plus any fields that say where it came from.
- Words for the parts of the system. A read is one half-hour forecasting moment. An answer is one Claude reply. The final forecast is the average of a read's answers, and it is the one that gets graded. A test variant is an experiment, and its value says what was changed (production is none). A precedent is a past moment that looks like now. Claude input is the frozen market data Claude was shown: claude_input_sha256 fingerprints it and input_field_path points into it. A flat edge is the distance either way from the read price inside which a move counts as flat.
- Prefixes. checked_ means after code's checks, as opposed to answer_parsed. existing_system_ means copied from Will's existing SPX forecasting system, not computed here. situation_ means a market tag frozen at read time. horizons_ starts a list of horizon names and is followed by what happened to them.
- No line has two fields with the same name at different levels. Inside a named group, a field may drop the group's prefix (existing_system_result_check.direction).
- Line layout: every line starts with line_type, then the join keys (flat), then named groups. A status always sits inside its group.
- Scores columns: a column is the JSON path with dots turned into underscores, and the horizon level is dropped because horizon is its own column (result.direction becomes result_direction; forecast.<horizon>.status becomes forecast_status).
- Same-phase knock-ons outside reads and outcomes. In payloads/ and precedent_set: payload_id becomes claude_input_sha256 and base_rate_shown becomes base_rate_shown_to_claude. eras.jsonl becomes prompt_and_model_versions.jsonl. In the payload ask: the horizon keys take the new names, order becomes direction_order_asked with values up_pct/flat_pct/down_pct, min becomes window_minutes, graded_on becomes graded_move_measured_to with the new values, and edge_sig becomes flat_edge_sig. In the rulebook: UNITS says 'flat edge' instead of 'edge' and its bucket sentence uses the 7 keys, and MATCHING says 'weight' instead of 'share'. session_idx is dropped everywhere.

## Four changes that go beyond renaming (approved by Will 10-09)

1. **Replace "KL in nats".** It becomes `largest_gap_from_base_rate_pct_points`: the biggest difference, in percentage points, between Claude's chances and the base rate.
2. **Move the "leans with the last 30 minutes" flag** inside each horizon, as `is_leaning_same_way_as_last_30_minutes_move_vs_base_rate`.
3. **Add `base_rate_shown_to_claude` to each horizon** on the reads lines, so a line can be read without opening the payload.
4. **Drop `session_idx`.** Nothing produces it.

## How the blind readers did

Before the fixes, on the merged names: by my count of their lists, Reader 1 marked 32 names GUESS and 1 LOST, and Reader 2 marked 28 GUESS and 4 LOST, out of about 95 fields each. The worst spots were the same for both readers:
- the 'edge' unit;
- the station_ prefix;
- the 'seal' words;
- 'nats';
- two fields named forecast, and two named similar_precedents, at different levels of the answer line;
- the 'payload' and 'scene' jargon.

After the fixes, every GUESS or LOST name is renamed or explained in the rename map. Four names were kept on purpose, because the readers had them right:
- read_id: locked as the join key. Both readers guessed its meaning correctly; the glossary must define 'read'.
- model_turn_count: both readers read it correctly.
- flat_edge_points: its meaning was guessed right. The 'either way?' doubt is now answered by the _flat_edges unit and the flat_within_1_flat_edge bucket.
- horizons_size_split_discarded: the old meaning was read right, and what replaces the split shows on the same line as null.

Four changes go beyond renaming and need Will's yes:
- KL in nats is replaced by largest_gap_from_base_rate_pct_points.
- is_leaning_same_way_as_last_30_minutes_move_vs_base_rate moves inside each horizon.
- base_rate_shown_to_claude is added per horizon on the reads lines.
- session_idx is dropped, because it had no source.

The examples now use real station values: clock odds 18.3/65.4/16.3 and the station's move, edge, high/low, grade and Pool 2.

Still to do: no fresh blind reader has seen the new examples. A blind reader is Phase 1's done-check and should run before the names lock. The names most likely to still draw a GUESS are:
- picked_precedents_among_code_nearest_3_count (nearest by what?);
- shown_precedents_outcomes (the blend with the base rate);
- the out-of-scope card field values (range_pos, m60, price_minus_flip).

Nothing was edited on disk.

## Example: one Claude answer (reads/, `line_type: claude_answer`) and the final forecast (`line_type: final_forecast`)

```json
{
  "sample_line": {
    "line_type": "claude_answer",
    "answer_id": "live:2026-10-09T14:30:12.458122-04:00#answer_1",
    "read_id": "live:2026-10-09T14:30:12.458122-04:00",
    "claude_input_sha256": "sha256:e3be78dc3f2cd9ac",
    "prompt_and_model_version": "cr-1",
    "test_variant": "none",
    "direction_order_asked": [
      "up_pct",
      "flat_pct",
      "down_pct"
    ],
    "precedent_order_shown": "shuffled",
    "answer_text": "{\"similar_precedents\":[{\"precedent_id\":\"pd8d7b\",\"weight_pct_of_picked_precedents\":45,\"similar_on\":[\"range_pos\",\"price_minus_flip\",\"gamma\"],\"differs_most_on\":\"range\"},{\"precedent_id\":\"p0ee3a\",\"weight_pct_of_picked_precedents\":30,\"similar_on\":[\"price_minus_flip\",\"vix_vix3m\",\"price_minus_vwap\"],\"differs_most_on\":\"range\"},{\"precedent_id\":\"p7ebc0\",\"weight_pct_of_picked_precedents\":25,\"similar_on\":[\"range_pos\",\"price_minus_open\"],\"differs_most_on\":\"m60\"}],\"reasons\":[{\"input_field_path\":\"tape.rv30_vs_clock_x\",\"pushes_toward\":\"smaller_move\",\"horizon\":\"next_30_minutes\"},{\"input_field_path\":\"options.regime.0dte_oi\",\"pushes_toward\":\"smaller_move\",\"horizon\":\"all\"},{\"input_field_path\":\"scale.straddle_used_x\",\"pushes_toward\":\"smaller_move\",\"horizon\":\"to_close\"}],\"strongest_reason_against_lean\":{\"input_field_path\":\"flow.lean_30m_vs_usual\",\"pushes_toward\":\"up\",\"horizon\":\"next_30_minutes\"},\"forecast\":{\"next_30_minutes\":{\"up_pct\":10,\"flat_pct\":70,\"down_pct\":20,\"up_by_size_pct\":{\"up_1_to_2_flat_edges\":7,\"up_2_to_3_flat_edges\":2,\"up_over_3_flat_edges\":1},\"down_by_size_pct\":{\"down_1_to_2_flat_edges\":14,\"down_2_to_3_flat_edges\":4,\"down_over_3_flat_edges\":2}},\"next_60_minutes\":{\"up_pct\":14,\"flat_pct\":68,\"down_pct\":18,\"up_by_size_pct\":{\"up_1_to_2_flat_edges\":11,\"up_2_to_3_flat_edges\":2,\"up_over_3_flat_edges\":1},\"down_by_size_pct\":{\"down_1_to_2_flat_edges\":12,\"down_2_to_3_flat_edges\":4,\"down_over_3_flat_edges\":2}},\"to_close\":{\"up_pct\":8,\"flat_pct\":72,\"down_pct\":20,\"up_by_size_pct\":{\"up_1_to_2_flat_edges\":7,\"up_2_to_3_flat_edges\":1,\"up_over_3_flat_edges\":0},\"down_by_size_pct\":{\"down_1_to_2_flat_edges\":14,\"down_2_to_3_flat_edges\":4,\"down_over_3_flat_edges\":2}}}}",
    "answer_parsed": {
      "similar_precedents": [
        {
          "precedent_id": "pd8d7b",
          "weight_pct_of_picked_precedents": 45,
          "similar_on": [
            "range_pos",
            "price_minus_flip",
            "gamma"
          ],
          "differs_most_on": "range"
        },
        {
          "precedent_id": "p0ee3a",
          "weight_pct_of_picked_precedents": 30,
          "similar_on": [
            "price_minus_flip",
            "vix_vix3m",
            "price_minus_vwap"
          ],
          "differs_most_on": "range"
        },
        {
          "precedent_id": "p7ebc0",
          "weight_pct_of_picked_precedents": 25,
          "similar_on": [
            "range_pos",
            "price_minus_open"
          ],
          "differs_most_on": "m60"
        }
      ],
      "reasons": [
        {
          "input_field_path": "tape.rv30_vs_clock_x",
          "pushes_toward": "smaller_move",
          "horizon": "next_30_minutes"
        },
        {
          "input_field_path": "options.regime.0dte_oi",
          "pushes_toward": "smaller_move",
          "horizon": "all"
        },
        {
          "input_field_path": "scale.straddle_used_x",
          "pushes_toward": "smaller_move",
          "horizon": "to_close"
        }
      ],
      "strongest_reason_against_lean": {
        "input_field_path": "flow.lean_30m_vs_usual",
        "pushes_toward": "up",
        "horizon": "next_30_minutes"
      },
      "forecast": {
        "next_30_minutes": {
          "up_pct": 10,
          "flat_pct": 70,
          "down_pct": 20,
          "up_by_size_pct": {
            "up_1_to_2_flat_edges": 7,
            "up_2_to_3_flat_edges": 2,
            "up_over_3_flat_edges": 1
          },
          "down_by_size_pct": {
            "down_1_to_2_flat_edges": 14,
            "down_2_to_3_flat_edges": 4,
            "down_over_3_flat_edges": 2
          }
        },
        "next_60_minutes": {
          "up_pct": 14,
          "flat_pct": 68,
          "down_pct": 18,
          "up_by_size_pct": {
            "up_1_to_2_flat_edges": 11,
            "up_2_to_3_flat_edges": 2,
            "up_over_3_flat_edges": 1
          },
          "down_by_size_pct": {
            "down_1_to_2_flat_edges": 12,
            "down_2_to_3_flat_edges": 4,
            "down_over_3_flat_edges": 2
          }
        },
        "to_close": {
          "up_pct": 8,
          "flat_pct": 72,
          "down_pct": 20,
          "up_by_size_pct": {
            "up_1_to_2_flat_edges": 7,
            "up_2_to_3_flat_edges": 1,
            "up_over_3_flat_edges": 0
          },
          "down_by_size_pct": {
            "down_1_to_2_flat_edges": 14,
            "down_2_to_3_flat_edges": 4,
            "down_over_3_flat_edges": 2
          }
        }
      }
    },
    "answer_checks": {
      "removed_invalid_items": [],
      "horizons_rescaled_to_sum_100": [],
      "horizons_rejected_sum_far_from_100": [],
      "horizons_size_split_fixed_off_by_1": [],
      "horizons_size_split_discarded": [],
      "valid_reason_count": 3
    },
    "checked_forecast": {
      "next_30_minutes": {
        "status": "ok",
        "up_pct": 10,
        "flat_pct": 70,
        "down_pct": 20,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 14,
          "flat_within_1_flat_edge": 70,
          "up_1_to_2_flat_edges": 7,
          "up_2_to_3_flat_edges": 2,
          "up_over_3_flat_edges": 1
        }
      },
      "next_60_minutes": {
        "status": "ok",
        "up_pct": 14,
        "flat_pct": 68,
        "down_pct": 18,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 12,
          "flat_within_1_flat_edge": 68,
          "up_1_to_2_flat_edges": 11,
          "up_2_to_3_flat_edges": 2,
          "up_over_3_flat_edges": 1
        }
      },
      "to_close": {
        "status": "ok",
        "up_pct": 8,
        "flat_pct": 72,
        "down_pct": 20,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 14,
          "flat_within_1_flat_edge": 72,
          "up_1_to_2_flat_edges": 7,
          "up_2_to_3_flat_edges": 1,
          "up_over_3_flat_edges": 0
        }
      }
    },
    "checked_similar_precedents": [
      {
        "precedent_id": "pd8d7b",
        "read_id": "seed:2026-10-05T14:30",
        "weight_pct_of_picked_precedents": 45
      },
      {
        "precedent_id": "p0ee3a",
        "read_id": "seed:2026-09-25T14:00",
        "weight_pct_of_picked_precedents": 30
      },
      {
        "precedent_id": "p7ebc0",
        "read_id": "seed:2026-07-24T14:00",
        "weight_pct_of_picked_precedents": 25
      }
    ],
    "call_stats": {
      "cost_usd": 0.12,
      "input_tokens": 6100,
      "cache_read_input_tokens": 1900,
      "cache_creation_input_tokens": 0,
      "output_tokens": 640,
      "model_turn_count": 1,
      "response_seconds": 48.3
    },
    "error": null
  },
  "final_line": {
    "line_type": "final_forecast",
    "read_id": "live:2026-10-09T14:30:12.458122-04:00",
    "claude_input_sha256": "sha256:e3be78dc3f2cd9ac",
    "prompt_and_model_version": "cr-1",
    "test_variant": "none",
    "answer_ids": [
      "live:2026-10-09T14:30:12.458122-04:00#answer_1",
      "live:2026-10-09T14:30:12.458122-04:00#answer_2"
    ],
    "usable_answer_count": 2,
    "any_answer_has_valid_reason": true,
    "picked_precedents_among_code_nearest_3_count": 2,
    "forecast": {
      "next_30_minutes": {
        "status": "ok",
        "up_pct": 11,
        "flat_pct": 68,
        "down_pct": 21,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4.5,
          "down_1_to_2_flat_edges": 14.5,
          "flat_within_1_flat_edge": 68,
          "up_1_to_2_flat_edges": 7.5,
          "up_2_to_3_flat_edges": 2.5,
          "up_over_3_flat_edges": 1
        },
        "answer_1": {
          "up_pct": 10,
          "flat_pct": 70,
          "down_pct": 20
        },
        "answer_2": {
          "up_pct": 12,
          "flat_pct": 66,
          "down_pct": 22
        },
        "largest_gap_between_answers_pct_points": 4,
        "base_rate_shown_to_claude": {
          "up_pct": 19.3,
          "flat_pct": 59.6,
          "down_pct": 21.1
        },
        "largest_gap_from_base_rate_pct_points": 8.4,
        "is_within_3_pct_points_of_base_rate": false,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": false
      },
      "next_60_minutes": {
        "status": "ok",
        "up_pct": 15,
        "flat_pct": 67,
        "down_pct": 18,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 12,
          "flat_within_1_flat_edge": 67,
          "up_1_to_2_flat_edges": 11.5,
          "up_2_to_3_flat_edges": 2.5,
          "up_over_3_flat_edges": 1
        },
        "answer_1": {
          "up_pct": 14,
          "flat_pct": 68,
          "down_pct": 18
        },
        "answer_2": {
          "up_pct": 16,
          "flat_pct": 66,
          "down_pct": 18
        },
        "largest_gap_between_answers_pct_points": 2,
        "base_rate_shown_to_claude": {
          "up_pct": 24.6,
          "flat_pct": 63.2,
          "down_pct": 12.3
        },
        "largest_gap_from_base_rate_pct_points": 9.6,
        "is_within_3_pct_points_of_base_rate": false,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": false
      },
      "to_close": {
        "status": "ok",
        "up_pct": 9,
        "flat_pct": 71,
        "down_pct": 20,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4.5,
          "down_1_to_2_flat_edges": 13.5,
          "flat_within_1_flat_edge": 71,
          "up_1_to_2_flat_edges": 7.5,
          "up_2_to_3_flat_edges": 1.5,
          "up_over_3_flat_edges": 0
        },
        "answer_1": {
          "up_pct": 8,
          "flat_pct": 72,
          "down_pct": 20
        },
        "answer_2": {
          "up_pct": 10,
          "flat_pct": 70,
          "down_pct": 20
        },
        "largest_gap_between_answers_pct_points": 2,
        "base_rate_shown_to_claude": {
          "up_pct": 12.3,
          "flat_pct": 66.7,
          "down_pct": 21.1
        },
        "largest_gap_from_base_rate_pct_points": 4.3,
        "is_within_3_pct_points_of_base_rate": false,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": false
      }
    }
  },
  "arm_line": {
    "line_type": "test_variant_forecast",
    "read_id": "live:2026-10-09T14:30:12.458122-04:00",
    "claude_input_sha256": "sha256:e3be78dc3f2cd9ac",
    "prompt_and_model_version": "cr-1",
    "test_variant": "no_precedents_shown",
    "test_variant_version": 1,
    "test_variant_claude_input_sha256": "sha256:9a41c7e05d2b6f83",
    "answer_ids": [
      "live:2026-10-09T14:30:12.458122-04:00#test:no_precedents_shown:answer_1"
    ],
    "usable_answer_count": 1,
    "any_answer_has_valid_reason": true,
    "picked_precedents_among_code_nearest_3_count": null,
    "forecast": {
      "next_30_minutes": {
        "status": "ok",
        "up_pct": 19,
        "flat_pct": 61,
        "down_pct": 20,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 14,
          "flat_within_1_flat_edge": 61,
          "up_1_to_2_flat_edges": 13,
          "up_2_to_3_flat_edges": 4,
          "up_over_3_flat_edges": 2
        },
        "base_rate_shown_to_claude": {
          "up_pct": 19.3,
          "flat_pct": 59.6,
          "down_pct": 21.1
        },
        "largest_gap_from_base_rate_pct_points": 1.4,
        "is_within_3_pct_points_of_base_rate": true,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": false
      },
      "next_60_minutes": {
        "status": "ok",
        "up_pct": 24,
        "flat_pct": 63,
        "down_pct": 13,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 1,
          "down_2_to_3_flat_edges": 3,
          "down_1_to_2_flat_edges": 9,
          "flat_within_1_flat_edge": 63,
          "up_1_to_2_flat_edges": 17,
          "up_2_to_3_flat_edges": 5,
          "up_over_3_flat_edges": 2
        },
        "base_rate_shown_to_claude": {
          "up_pct": 24.6,
          "flat_pct": 63.2,
          "down_pct": 12.3
        },
        "largest_gap_from_base_rate_pct_points": 0.7,
        "is_within_3_pct_points_of_base_rate": true,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": true
      },
      "to_close": {
        "status": "ok",
        "up_pct": 12,
        "flat_pct": 68,
        "down_pct": 20,
        "size_buckets_pct": {
          "down_over_3_flat_edges": 2,
          "down_2_to_3_flat_edges": 4,
          "down_1_to_2_flat_edges": 14,
          "flat_within_1_flat_edge": 68,
          "up_1_to_2_flat_edges": 9,
          "up_2_to_3_flat_edges": 2,
          "up_over_3_flat_edges": 1
        },
        "base_rate_shown_to_claude": {
          "up_pct": 12.3,
          "flat_pct": 66.7,
          "down_pct": 21.1
        },
        "largest_gap_from_base_rate_pct_points": 1.3,
        "is_within_3_pct_points_of_base_rate": true,
        "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": false
      }
    }
  }
}
```

## Example: one outcome line (outcomes/, one per read and horizon)

```json
{
  "line_type": "outcome",
  "read_id": "live:2026-10-09T14:30:12.458122-04:00",
  "claude_input_sha256": "sha256:e3be78dc3f2cd9ac",
  "horizon": "next_30_minutes",
  "grading_rule_version": 1,
  "finalize_attempt_number": 1,
  "finalized_at": "2026-10-09T17:15:04-04:00",
  "trading_day": "2026-10-09",
  "half_hour_slot_et": "14:30",
  "prompt_and_model_version": "cr-1",
  "read_source": "live",
  "result": {
    "status": "final",
    "graded_move_measured_to": "window_average_price",
    "window_minutes": 30,
    "price_at_read": 7813.01,
    "flat_edge_points": 3.12,
    "graded_move_points": 0.7,
    "graded_move_flat_edges": 0.22,
    "direction": "flat",
    "size_bucket": "flat_within_1_flat_edge",
    "highest_move_in_window_flat_edges": 1.96,
    "lowest_move_in_window_flat_edges": -0.61,
    "price_bars_sha256": "sha256:5c1f0a9e7b3d2481"
  },
  "comparison_forecasts": {
    "base_rate_shown_to_claude": {
      "up_pct": 19.3,
      "flat_pct": 59.6,
      "down_pct": 21.1
    },
    "existing_system_time_of_day_odds_20_sessions": {
      "up_pct": 18.3,
      "flat_pct": 65.4,
      "down_pct": 16.3,
      "forecasts_move_measured_to": "window_average_price"
    },
    "existing_system_main_forecast": {
      "up_pct": 23.6,
      "flat_pct": 61.6,
      "down_pct": 14.8,
      "is_written_live": true,
      "written_at": "2026-10-09T14:30:40-04:00",
      "written_after_read_seconds": 28
    },
    "shown_precedents_outcomes": {
      "up_pct": 12.9,
      "flat_pct": 63.1,
      "down_pct": 24.1,
      "precedent_count": 10,
      "base_rate_blended_in_as_precedent_count": 20
    }
  },
  "missing_comparison_forecasts": {},
  "existing_system_result_check": {
    "direction": "flat",
    "grading_rule_version": 3,
    "is_same_direction": true
  }
}
```

## Example: the reply Claude returns

```json
{
  "similar_precedents": [
    {
      "precedent_id": "pd8d7b",
      "weight_pct_of_picked_precedents": 45,
      "similar_on": [
        "range_pos",
        "price_minus_flip",
        "gamma"
      ],
      "differs_most_on": "range"
    },
    {
      "precedent_id": "p0ee3a",
      "weight_pct_of_picked_precedents": 30,
      "similar_on": [
        "price_minus_flip",
        "vix_vix3m",
        "price_minus_vwap"
      ],
      "differs_most_on": "range"
    },
    {
      "precedent_id": "p7ebc0",
      "weight_pct_of_picked_precedents": 25,
      "similar_on": [
        "range_pos",
        "price_minus_open"
      ],
      "differs_most_on": "m60"
    }
  ],
  "reasons": [
    {
      "input_field_path": "tape.rv30_vs_clock_x",
      "pushes_toward": "smaller_move",
      "horizon": "next_30_minutes"
    },
    {
      "input_field_path": "options.regime.0dte_oi",
      "pushes_toward": "smaller_move",
      "horizon": "all"
    },
    {
      "input_field_path": "scale.straddle_used_x",
      "pushes_toward": "smaller_move",
      "horizon": "to_close"
    }
  ],
  "strongest_reason_against_lean": {
    "input_field_path": "flow.lean_30m_vs_usual",
    "pushes_toward": "up",
    "horizon": "next_30_minutes"
  },
  "forecast": {
    "next_30_minutes": {
      "up_pct": 10,
      "flat_pct": 70,
      "down_pct": 20,
      "up_by_size_pct": {
        "up_1_to_2_flat_edges": 7,
        "up_2_to_3_flat_edges": 2,
        "up_over_3_flat_edges": 1
      },
      "down_by_size_pct": {
        "down_1_to_2_flat_edges": 14,
        "down_2_to_3_flat_edges": 4,
        "down_over_3_flat_edges": 2
      }
    },
    "next_60_minutes": {
      "up_pct": 14,
      "flat_pct": 68,
      "down_pct": 18,
      "up_by_size_pct": {
        "up_1_to_2_flat_edges": 11,
        "up_2_to_3_flat_edges": 2,
        "up_over_3_flat_edges": 1
      },
      "down_by_size_pct": {
        "down_1_to_2_flat_edges": 12,
        "down_2_to_3_flat_edges": 4,
        "down_over_3_flat_edges": 2
      }
    },
    "to_close": {
      "up_pct": 8,
      "flat_pct": 72,
      "down_pct": 20,
      "up_by_size_pct": {
        "up_1_to_2_flat_edges": 7,
        "up_2_to_3_flat_edges": 1,
        "up_over_3_flat_edges": 0
      },
      "down_by_size_pct": {
        "down_1_to_2_flat_edges": 14,
        "down_2_to_3_flat_edges": 4,
        "down_over_3_flat_edges": 2
      }
    }
  }
}
```

## Rulebook OUTPUT section (as `spx_claude_forecast/rulebook_cr-1.txt` carries it)

```
OUTPUT. Reply with one JSON object and nothing else.
similar_precedents: list of {precedent_id, weight_pct_of_picked_precedents, similar_on: [field], differs_most_on: field}; one to four rows; weight_pct_of_picked_precedents values are whole numbers that sum to one hundred.
reasons: two to four {input_field_path, pushes_toward: up|down|bigger_move|smaller_move, horizon: next_30_minutes|next_60_minutes|to_close|all}; input_field_path is a dotted path that exists in SCENE and is not listed in absent.
strongest_reason_against_lean: one {input_field_path, pushes_toward, horizon}: the strongest fact against your lean.
forecast: one object per horizon in ask.horizons, keyed by that horizon's name, with up_pct, flat_pct and down_pct in the order ask.direction_order_asked gives, then up_by_size_pct: {up_1_to_2_flat_edges, up_2_to_3_flat_edges, up_over_3_flat_edges} and down_by_size_pct: {down_1_to_2_flat_edges, down_2_to_3_flat_edges, down_over_3_flat_edges}; whole numbers; up_pct, flat_pct and down_pct sum to one hundred; up_by_size_pct sums to up_pct; down_by_size_pct sums to down_pct.
No other keys. No prose.
```

## Full rename map (121 fields)

| Record | Old | New | Meaning |
|---|---|---|---|
| reads_sample | `(none)` | `line_type` | What kind of line this is; on this line it is "claude_answer" (text). |
| reads_sample | `sample_id` | `answer_id` | ID of one Claude answer: read_id plus "#answer_1" or "#answer_2". Test answers use "#test:<variant>:answer_<n>" (text). |
| reads_sample | `read_id` | `read_id` | Unchanged, because it is the station's join key. ID of one read, meaning one half-hour forecasting moment: "live:<timestamp>" or "seed:<day>T<slot>" (text). The glossary in record_formats.md must define 'read' on its first line. |
| reads_sample | `payload_id` | `claude_input_sha256` | Fingerprint of the market input Claude was shown. It ignores the order of the precedents, so both answers share it. It joins payloads, reads and outcomes (sha256 text). Renamed because it is a hash, and fingerprints end in _sha256; 'payload' was jargon. |
| reads_sample | `era` | `prompt_and_model_version` | One version label for the rulebook, the input builder and the model used together, for example "cr-1" (text). 'setup' did not say what was versioned. |
| reads_sample | `(none)` | `test_variant` | Which experiment made this answer; "none" means production (text). |
| reads_sample | `(none)` | `test_variant_version` | Version of that experiment. Appears on test answers only (integer). |
| reads_sample | `arm_payload_id` | `test_variant_claude_input_sha256` | Fingerprint of the changed input a test variant actually sent. Appears on test answers only (sha256 text). |
| reads_sample | `order` | `direction_order_asked` | The order Claude was asked to write the three chances in, for example ["up_pct","flat_pct","down_pct"] (list of key names). |
| reads_sample | `(none)` | `precedent_order_shown` | The order the precedents were shown in: "shuffled" for answer 1 and "reversed" for answer 2 (text). |
| reads_sample | `raw` | `answer_text` | Claude's reply word for word, at most 4,000 characters (text). |
| reads_sample | `parsed` | `answer_parsed` | Claude's reply read as JSON, before code's checks (object). |
| reads_sample | `checks` | `answer_checks` | What code removed or fixed in the reply (object). |
| reads_sample | `checks.deleted[]` | `answer_checks.removed_invalid_items[]` | Each removed piece of the reply, as {answer_path, removed_because} (list). |
| reads_sample | `checks.rescaled` | `answer_checks.horizons_rescaled_to_sum_100[]` | Horizons whose up/flat/down added to 98 to 102 and were rescaled to 100 (list of horizon names). |
| reads_sample | `checks.malformed[]` | `answer_checks.horizons_rejected_sum_far_from_100[]` | Horizons whose up/flat/down missed 100 by more than 2. Their status is "rejected" and they are scored as the base rate (list of horizon names). The name now says why they were rejected. |
| reads_sample | `(none)` | `answer_checks.horizons_size_split_fixed_off_by_1[]` | Horizons whose size split missed its total by 1 and was fixed on its largest bucket (list of horizon names). The name now says how it was fixed. |
| reads_sample | `(none)` | `answer_checks.horizons_size_split_discarded[]` | Horizons whose size split missed by more than 1. The split is thrown away, up/flat/down are kept, and that horizon's size_buckets_pct is null (list of horizon names). The readers understood it correctly; what replaces the split shows on the same line as null. |
| reads_sample | `checks.ungrounded` | `answer_checks.valid_reason_count` | How many reasons passed the checks; 0 means none did (count). |
| reads_sample | `p7` | `checked_forecast.<horizon>` | This answer after code's checks, one block per horizon: {status, up_pct, flat_pct, down_pct, size_buckets_pct} (percent). It is named apart from answer_parsed.forecast so the line never has two fields called forecast. |
| reads_sample | `p7 (7-number list)` | `checked_forecast.<horizon>.size_buckets_pct` | The chance of each of the 7 size buckets, as named keys from down_over_3_flat_edges to up_over_3_flat_edges. They add to 100 (percent). |
| reads_sample | `matched[]` | `checked_similar_precedents[]` | Claude's precedents after the checks, each {precedent_id, read_id, weight_pct_of_picked_precedents}. read_id is the real past read that the alias points to. Named apart from answer_parsed.similar_precedents. |
| reads_sample | `matched[].share` | `checked_similar_precedents[].weight_pct_of_picked_precedents` | How much Claude leaned on that precedent; all of them add to 100 (percent). |
| reads_sample | `cost_usd` | `call_stats.cost_usd` | What the call cost (US dollars). |
| reads_sample | `tokens.in` | `call_stats.input_tokens` | Input tokens not served from the cache. This is the CLI's own name (tokens). |
| reads_sample | `tokens.cache_read` | `call_stats.cache_read_input_tokens` | Input tokens read from the prompt cache (tokens). |
| reads_sample | `tokens.cache_write` | `call_stats.cache_creation_input_tokens` | Input tokens written to the prompt cache (tokens). |
| reads_sample | `tokens.out` | `call_stats.output_tokens` | Tokens Claude wrote (tokens). |
| reads_sample | `num_turns` | `call_stats.model_turn_count` | How many model turns the call took; 1 is normal (count). The name is kept, because both readers read it correctly. |
| reads_sample | `latency_s` | `call_stats.response_seconds` | Time from sending the call to getting the reply (seconds). |
| reads_sample | `error` | `error` | The error message, or null when the call worked (text). |
| reads_final | `arm: "prod"` | `line_type: "final_forecast" + test_variant: "none"` | This line is the graded forecast for one read: the average of its answers (text). |
| reads_final | `read_id, payload_id, era (implied, not written)` | `read_id, claude_input_sha256, prompt_and_model_version` | Written on the line so that it stands alone (text). |
| reads_final | `(none)` | `answer_ids[]` | The answer lines this forecast averages (list of answer_id). |
| reads_final | `h30 / h60 / to_close` | `forecast.next_30_minutes / .next_60_minutes / .to_close` | One group per horizon: the next 30 minutes, the next 60 minutes (left out at 15:30), and up to the official close. |
| reads_final | `status` | `forecast.<horizon>.status` | ok, rejected, not_asked or failed (text). |
| reads_final | `p3` | `forecast.<horizon>.up_pct / flat_pct / down_pct` | The graded chances that the move ends up, flat or down. They add to 100 (percent). |
| reads_final | `p7` | `forecast.<horizon>.size_buckets_pct` | The averaged chance of each of the 7 size buckets (percent). |
| reads_final | `s1_p3` | `forecast.<horizon>.answer_1` | Answer 1's up/flat/down (percent). |
| reads_final | `s2_p3` | `forecast.<horizon>.answer_2` | Answer 2's up/flat/down (percent). |
| reads_final | `s_gap` | `forecast.<horizon>.largest_gap_between_answers_pct_points` | The largest of the three up/flat/down differences between the two answers (percentage points). It now says 'largest'. |
| reads_final | `(none)` | `forecast.<horizon>.base_rate_shown_to_claude` | The base rate Claude was shown for this horizon, put on the line so the next two fields can be checked by eye (percent). |
| reads_final | `kl_vs_br` | `forecast.<horizon>.largest_gap_from_base_rate_pct_points` | The largest of the three differences between this forecast and the base rate Claude was shown (percentage points). It replaces the KL figure in nats, which no name could make readable. KL can still be worked out nightly from the stored numbers. This is the one change that alters a stored measure, so it needs Will's yes. |
| reads_final | `sat_on_br` | `forecast.<horizon>.is_within_3_pct_points_of_base_rate` | True when largest_gap_from_base_rate_pct_points is 3 or less, which means Claude mostly copied the base rate (yes/no). The every-horizon check is all three flags true. |
| reads_final | `leans_with_m30` | `forecast.<horizon>.is_leaning_same_way_as_last_30_minutes_move_vs_base_rate` | True when the forecast's larger side (up_pct against down_pct) matches the way the index moved in the 30 minutes before the read (yes/no). It now sits inside each horizon, so it is clear which forecast it judges. |
| reads_final | `grounded` | `any_answer_has_valid_reason` | At least one reason survived the checks (yes/no). |
| reads_final | `top3_overlap` | `picked_precedents_among_code_nearest_3_count` | How many of Claude's similar precedents are among the 3 nearest that code found by distance, from 0 to 3 (count). |
| reads_final | `n_samples_ok` | `usable_answer_count` | How many answers passed the checks, from 0 to 2 (count). |
| reads_arm | `(none)` | `line_type: "test_variant_forecast"` | Same shape as the final forecast line, for one experiment on one read. |
| reads_arm | `arm` | `test_variant` | Which experiment, named for what it changed (text): no_precedents_shown (was blind), random_precedents_shown (random_cards), no_base_rate_shown (no_base_rate), precedent_outcomes_shuffled (decoy), earlier_results_today_shown (with_today), five_answers_averaged (five_sample), own_track_record_shown (with_record). slot_prior keeps its name until the spec defines it. |
| reads_arm | `arm_v` | `test_variant_version` | Version of the experiment (integer). |
| reads_arm | `arm_payload_id` | `test_variant_claude_input_sha256` | Fingerprint of the changed input this variant sent. claude_input_sha256 stays production's (sha256 text). |
| reads_arm | `sample_id (#arm:<name>)` | `answer_ids[]` | Points at the variant's answer lines, "#test:<variant>:answer_<n>". This also covers five answers. |
| reads_arm | `(shape)` | `(shape)` | answer_1, answer_2 and largest_gap_between_answers_pct_points appear only when the variant made 2 or more answers. picked_precedents_among_code_nearest_3_count is null when no precedents were shown. |
| claude_reply | `matched[]` | `similar_precedents[]` | The 1 to 4 past moments Claude picks as most like now (list). |
| claude_reply | `matched[].id` | `precedent_id` | The short alias Claude was shown for that past moment (text). |
| claude_reply | `matched[].share` | `weight_pct_of_picked_precedents` | How much Claude leans on that moment. Whole numbers; all of them add to 100 (percent). |
| claude_reply | `matched[].same_on[]` | `similar_on[]` | Fields where that moment matches now (list of field names). |
| claude_reply | `matched[].differs_on` | `differs_most_on` | The one field where it differs most from now (field name). |
| claude_reply | `reasons[]` | `reasons[]` | 2 to 4 reasons for the forecast (list). |
| claude_reply | `reasons[].field` | `input_field_path` | Dotted path to the input field the reason rests on. It must exist in the input and must not be listed as absent (text). This replaces 'scene', which a newcomer cannot place. |
| claude_reply | `reasons[].pushes` | `pushes_toward` | Which way the reason pushes: up, down, bigger_move or smaller_move (was up, down, move or still). |
| claude_reply | `reasons[].for` | `horizon` | Which horizon the reason applies to: next_30_minutes, next_60_minutes, to_close or all. |
| claude_reply | `against` | `strongest_reason_against_lean` | The strongest fact against Claude's own lean, with the same three fields as a reason. |
| claude_reply | `h30 / h60 / to_close` | `forecast.next_30_minutes / .next_60_minutes / .to_close` | One block for each horizon asked. |
| claude_reply | `up / flat / down` | `up_pct / flat_pct / down_pct` | The chance the move ends up, flat or down. Whole numbers that add to 100 (percent). |
| claude_reply | `up_size [U1, U2, U3]` | `up_by_size_pct {up_1_to_2_flat_edges, up_2_to_3_flat_edges, up_over_3_flat_edges}` | up_pct split by how far up the move goes, in flat edges. It adds to up_pct (percent). |
| claude_reply | `down_size [D1, D2, D3]` | `down_by_size_pct {down_1_to_2_flat_edges, down_2_to_3_flat_edges, down_over_3_flat_edges}` | down_pct split by how far down the move goes. It adds to down_pct (percent). Named keys end the trap of not knowing whether the nearest or the farthest bucket comes first. |
| outcomes | `(none)` | `line_type: "outcome"` | What kind of line this is (text). |
| outcomes | `read_id` | `read_id` | Unchanged join key: the read this result belongs to (text). |
| outcomes | `payload_id` | `claude_input_sha256` | Fingerprint of the input Claude was shown for this read (sha256 text). |
| outcomes | `horizon` | `horizon` | Which window this line grades: next_30_minutes, next_60_minutes or to_close (text). |
| outcomes | `rule_version` | `grading_rule_version` | Version of this project's grading rules. It is numbered separately from the existing system's (integer). |
| outcomes | `seal_seq` | `finalize_attempt_number` | Which attempt at finalizing this result this is. A no_data horizon that is finalized later gets 2 (integer). 'seal' was jargon. |
| outcomes | `sealed_at` | `finalized_at` | When the result was written for good, no earlier than the close plus 75 minutes (timestamp, Eastern time). |
| outcomes | `day` | `trading_day` | The market date (YYYY-MM-DD). |
| outcomes | `slot` | `half_hour_slot_et` | The read's half-hour slot (HH:MM, Eastern time). |
| outcomes | `session_idx` | `(removed)` | Dropped. It had no source, because sessions.py has no day counter, and trading_day already names the session. The library can number days from the market calendar if it needs to. |
| outcomes | `era` | `prompt_and_model_version` | Version label for the rulebook, input builder and model together (text). |
| outcomes | `origin` | `read_source` | Where the read came from: live, seed (rebuilt from stored data), rebuilt_live or bars_5m (text). |
| outcomes | `status` | `result.status` | final, no_data or not_asked (was sealed, no_data or not_asked). |
| outcomes | `close_src (and the implied grading basis)` | `result.graded_move_measured_to` | The price the move is measured to, starting from price_at_read (text): window_average_price for the 30- and 60-minute horizons, official_close_price for to_close, or last_bar_close_price when the official close is missing. |
| outcomes | `minutes` | `result.window_minutes` | Length of the window (minutes). |
| outcomes | `from_price` | `result.price_at_read` | The S&P 500 level at the read (index price). |
| outcomes | `edge_pts` | `result.flat_edge_points` | A move within this many points either way of price_at_read counts as flat. It is also the size of one flat edge (index points). The name is kept; the doubt about 'either way' is now answered by the _flat_edges unit and the flat_within_1_flat_edge bucket. |
| outcomes | `g_pts` | `result.graded_move_points` | The window's average price (or the close) minus price_at_read (index points). |
| outcomes | `edges` | `result.graded_move_flat_edges` | graded_move_points divided by flat_edge_points (flat edges). |
| outcomes | `label3` | `result.direction` | up, flat or down (text). |
| outcomes | `bucket7` | `result.size_bucket` | Which of the 7 size buckets the move fell in, by name (text). |
| outcomes | `best_edges` | `result.highest_move_in_window_flat_edges` | The highest the index got above price_at_read during the window, checked minute by minute (flat edges). 30- and 60-minute horizons only. 'move' makes clear it is a change, not a price. |
| outcomes | `worst_edges` | `result.lowest_move_in_window_flat_edges` | The lowest the index got, measured the same way. Negative means below the read price (flat edges). |
| outcomes | `bars_sha` | `result.price_bars_sha256` | Fingerprint of the price bars used (sha256 text). |
| outcomes | `refs` | `comparison_forecasts` | The other forecasts Claude is scored against, each {up_pct, flat_pct, down_pct} plus where it came from. |
| outcomes | `refs.base_rate_shown` | `comparison_forecasts.base_rate_shown_to_claude` | How this time of day ended on earlier sessions, as Claude was shown it (percent). The name now says who it was shown to. |
| outcomes | `refs.clock / refs.clock_endprice` | `comparison_forecasts.existing_system_time_of_day_odds_20_sessions` | The existing system's usual odds for this time of day (percent), plus forecasts_move_measured_to: window_average_price, or window_end_price at 60 minutes. |
| outcomes | `refs.pool_v2` | `comparison_forecasts.existing_system_main_forecast` | The existing system's live headline forecast, Pool 2, which Will calls the combined forecast (percent). 'main' says its role without raising 'combined of what?'. |
| outcomes | `ref_src` | `existing_system_main_forecast.is_written_live` | True when it was written live and is not a later replay (yes/no). Replays are never used; they go into missing_comparison_forecasts. |
| outcomes | `ref_created_at` | `existing_system_main_forecast.written_at + written_after_read_seconds` | When it was written (timestamp, converted to Eastern time) and how long after the read (seconds). The second number must be 600 or less. |
| outcomes | `refs.knn_code` | `comparison_forecasts.shown_precedents_outcomes` | Odds built from how the shown precedents turned out, blended with the base rate (percent). It carries precedent_count and base_rate_blended_in_as_precedent_count, the number of precedents the base rate is weighted as, so the blend is visible. |
| outcomes | `refs.ref_missing` | `missing_comparison_forecasts` | {forecast name: reason} for each comparison forecast that could not be used. |
| outcomes | `cross_check` | `existing_system_result_check` | A check of this result against the existing system's own grade of the same read (30- and 60-minute horizons only). |
| outcomes | `cross_check.integral_grades_outcome` | `existing_system_result_check.direction` | The direction the existing system graded: up, flat or down (text). |
| outcomes | `(none)` | `existing_system_result_check.grading_rule_version` | The existing system's own grading-rule version (integer). |
| outcomes | `cross_check.agree` | `existing_system_result_check.is_same_direction` | Whether it matches result.direction (yes/no). |
| scores | `day / slot / era / origin / rule_version` | `trading_day / half_hour_slot_et / prompt_and_model_version / read_source / grading_rule_version` | Same names as on the outcome line. |
| scores | `session_idx` | `(removed)` | Dropped, as on the outcome line. |
| scores | `payload_id` | `claude_input_sha256` | Input fingerprint (sha256 text). |
| scores | `arm` | `test_variant` | Which experiment; none means production (text). |
| scores | `voice` | `forecaster` | Who made the forecast being scored: claude_final, claude_answer_1, claude_answer_2, base_rate_shown_to_claude, existing_system_time_of_day_odds_20_sessions, existing_system_main_forecast or shown_precedents_outcomes (text). |
| scores | `status` | `forecast_status` | The forecast's status (text). |
| scores | `label3 / bucket7` | `result_direction / result_size_bucket` | The actual result (text). |
| scores | `p_up / p_flat / p_down` | `up_pct / flat_pct / down_pct` | The forecaster's chances (percent, 0 to 100; the scorer divides by 100). |
| scores | `conf` | `top_choice_pct` | The largest of the three chances. It is not a confidence Claude stated (percent). |
| scores | `ll3` | `log_loss` | Three-way log loss with a 2% floor, the station's formula (nats; lower is better). |
| scores | `move_loss / dir_loss` | `log_loss_move_part / log_loss_direction_part` | The 'did it move' and 'which way' parts of log_loss; together they add up to log_loss (nats). |
| scores | `brier3` | `brier_score` | Three-way Brier score (lower is better). |
| scores | `rps7` | `size_ranked_probability_score` | Ranked probability score over the 7 size buckets, divided by 6 (lower is better). |
| scores | `right3` | `is_top_choice_correct` | Whether the largest chance matched the result (yes/no). |
| scores | `score_v` | `scoring_code_version` | Version of the scoring code (text). |
| scores | `sit_slot` | `(removed)` | Repeats half_hour_slot_et. |
| scores | `sit_phase / sit_gamma / sit_book / sit_event / sit_trend / sit_range_pos (sit_rpos) / sit_vix_term` | `situation_day_phase / situation_dealer_gamma / situation_options_book / situation_scheduled_event / situation_last_60_minutes_trend / situation_position_in_day_range / situation_vix_term_structure` | Market tags frozen at read time, used to split the track record (text). |
