# Final cr-1 mockup for the 2026-10-09 14:30:12 live read, pressure-test fixes applied. Values from fix_lib2.py + read-only store pulls.
import json, hashlib, random, math
D = '/private/tmp/claude-501/-Users-will-Desktop-Mirai-Awakening/3ffd3e1c-b125-40a7-bbc5-e45838ebcfe6/scratchpad/payload_v1'
lib = json.load(open(f'{D}/fix_lib.json'))
def rv(v, a, b): return {"v": v, "r": [a, b]}
cards = [{k: (0.0 if v == 0 and isinstance(v, float) else v) for k, v in c.items() if not k.startswith('_')} for c in lib['cards']]
code_order = [c['id'] for c in cards]
code_dist = {c['id']: c['_d'] for c in lib['cards']}
n = lib['now']
now = {"id": "now", "ago": 0, "at": "14:30", "src": "live", "book": "native", "gamma": "long", "m30": 0.03, "m60": 0.07,
       "price_minus_close": 0.63, "price_minus_open": 0.27, "range_pos": 0.95, "range": 0.46, "price_minus_vwap": 0.20,
       "price_minus_flip": 0.55, "gap": 0.36, "vix_vix3m": 0.83}
BR = lib['base_rate']
scene = {
 "base_rate": {"slot": "14:30", "sessions": lib['base_sessions'], "from": {"seed": lib['base_sessions'], "live": 0},
   "bucket_order": ["D3", "D2", "D1", "F", "U1", "U2", "U3"], "h30": BR['h30'], "h60": BR['h60'], "to_close": BR['to_close']},
 "data_sources": {"cut_at": "14:30:12", "spx_bars": {"last_finished": "14:29", "age_s": 12},
   "options_diary": {"row_at": "14:30:12", "age_s": 0, "book": "native", "coverage": "complete"},
   "market_feed": {"snapshot_at": "14:30:05", "age_s": 7, "failed": []},
   "tape": {"fold_at": "14:29:27", "age_s": 45, "trades_15m_vs_usual_x": 0.87, "content": "ok"},
   "headlines": {"last_capture": "14:26:25", "age_s": 227}, "dated_book": {"as_of": "08:17", "age_h": 6.2},
   "schwab_login_days_left": 3.83},
 "clock": {"min_after_open": 300, "min_to_close": 90, "phase": "fourth_fifth"},
 "calendar": {"day_class": "medium-tier day", "today": [{"kind": "UMICH", "at": "10:00", "tier": "medium", "min_ago": 270}],
   "next_high_tier": {"kind": "CPI", "sessions_ahead": 3}, "fomc": {"phase": "none", "sessions_to_next": 13},
   "month": {"trading_day": 7, "days_left": 15, "turn_window": "none", "pension_stock_minus_bond_mtd_pts": rv(1.37, 53, 119)},
   "expiry": {"today": "daily", "monthly_sessions_ahead": 5}},
 "scale": {"sigma_src": "anchor", "sigma_live_x": 0.55, "atm_iv_pct": 8.44, "straddle_open_sig": 0.28, "straddle_left_sig": 0.076,
   "straddle_used_x": 1.32, "vix1d": 8.72, "vix1d_prior_close": 10.24, "realized_30m_vs_priced_x": 0.16},
 "tape": {"price_minus_close_sig": rv(0.63, 13, 19), "price_minus_open_sig": rv(0.27, 12, 19), "m30_sig": rv(0.03, 4, 19),
   "m60_sig": rv(0.07, 10, 19), "range_sig": rv(0.46, 3, 20), "range_pos": 0.95, "five_session_pos": 0.78,
   "price_minus_vwap_sig": rv(0.20, 16, 19), "vwap_last_touch_min_ago": 225,
   "high": {"minus_price_sig": 0.02, "at": "14:27", "min_ago": 3}, "low": {"minus_price_sig": -0.44, "at": "09:48", "min_ago": 282},
   "prior_high": {"minus_price_sig": -0.2, "price_above_for_min": 146}, "path_eff_since_open": 0.07,
   "open_crosses": rv(13, 15, 20), "rv30_vs_clock_x": rv(0.56, 1, 19),
   "last_6x5m_sig": [-0.031, 0.038, 0.004, 0.01, 0.01, -0.004],
   "half_hours_vs_close_sig": [0.28, 0.36, 0.47, 0.44, 0.45, 0.55, 0.57, 0.55, 0.6, 0.63]},
 "open_signals": {
   "gap": {"settled_sig": rv(0.36, 11, 19), "settled_pct": 0.35, "print_pct": 0.27, "x_straddle": 1.3, "filled": False, "kept_pct": 175,
     "fill_by_close_base_pct": 58, "fill_base_n": 536, "base_on": "print_gap_size"},
   "noise_band": {"sigma_pct": 0.337, "side": "above", "dist_bp": 0.1, "open_basis": "0930_bar"},
   "opt_imbalance_10m": {"tilt": 0.037, "trades": 16641, "feed_ok": True}},
 "close_signals": {"r1": {"bp": 26.8, "to": "10:00"},
   "gamma_open": {"regime": "long", "net_gex_usd_bn_per_1pct": {"0dte": 11.2, "0_7dte": 37.6}, "price_minus_flip_sig": 0.48,
     "book": "native", "at": "09:30"},
   "rrod_gate": "closed_long_gamma",
   "letf": {"usd_bn_per_1pct": 1.07, "buy_if_close_here_usd_bn": 0.65, "funds": 7, "shares_at": "09:25"}},
 "options": {"book": "native", "regime": {"0dte_oi": "long", "0dte_volume": "long", "0_7dte_oi": "long"},
   "net_gex_usd_bn_per_1pct": {"0dte": 36.3, "0_7dte": 82.9, "at_prior_close_0_7dte": -23.3},
   "gamma_above_pct": {"0dte": rv(58, 15, 19), "1_7dte": rv(68, 18, 19)}, "top_strike_share_pct": rv(9.9, 17, 19),
   "levels_minus_price_sig": [{"what": "gamma_flip", "sig": -0.55, "r": [13, 19]}, {"what": "call_side_heavy", "sig": 0.03},
     {"what": "charm_wall", "sig": 0.09}, {"what": "magnet", "sig": -0.17}, {"what": "put_side_heavy", "sig": -0.17},
     {"what": "oi_call_wall", "sig": 2.48}, {"what": "oi_put_wall_1_7dte", "sig": -4.16}],
   "heavy_strikes_moved_30m": False, "turnover_x_oi": 6.2, "skew_rr25_volpts": rv(0.3, 8, 18), "atm_iv_30m_volpts": 0.39},
 "flow": {"tilt_15m": rv(0.054, 15, 18), "told_share": 0.54, "lean_30m_vs_usual": rv(0.06, 15, 18), "big_prints_10m": 12,
   "big_prints_call_side_pct": rv(32, 7, 16), "premium_pace_30m": {"r": [0, 18]}, "spy_volume_30m_x": rv(0.4, 0, 20),
   "spy_spread_cents": 1},
 "internals": {"add_live_approx": 436, "add_30m_change": -54, "adv_share_pct": 58},
 "cross_asset": {"vix": 14.87, "vix_minus_prior_close": -0.54, "vix_vix3m": 0.833, "vix_vix3m_prior_close": 0.852,
   "backwardated": False, "vix9d_vix": 0.752, "ten_year_bp_since_close": rv(1.9, 3, 20)},
 "overnight": {"es_vs_close_sig": rv(0.28, 8, 19), "range_sig": rv(0.48, 4, 20), "gap_origin": "europe_open", "zn_pct": -0.19,
   "legs_sig": {"after_close": 0.07, "asia": 0.15, "europe_open": 0.13, "europe_morning": 0.0, "pre_report": -0.06,
     "report_window": -0.1, "last_stretch": 0.0}},
 "foreign": {"tokyo_ret_pct": -0.02, "europe_ret_pct": 0.95, "dax_ret_pct": 1.13, "europe_at": "11:30", "usdjpy_1d_pct": 0.26,
   "usdjpy_shock": False},
 "news": {"titles": "withheld", "captured_60m": 81, "index_mover_items_60m": 2, "kinds": ["fed"], "newest_index_mover_min_ago": 7,
   "stale_dropped": 1},
 "code": {"TREND-05": "one-way up", "TREND-06": "reversed", "TREND-10": "up failed", "LEVELS-07": "above overnight high",
   "OPTIONS-04": "held at call wall", "OPTIONS-05": "abandoned above", "VOLATILITY-05": "more fearful", "VOLATILITY-13": "risen",
   "BREADTH-01": "more down", "BREADTH-03": "new high, confirmed", "FLOW-02": "diverging up", "FLOW-08": "rotation"},
 "precedents": {"pool": {"sessions": lib['pool_sessions'], "rule": "slot +-45 min, same book, <=2 per session, earlier days, sealed, unit-checked"},
   "shown": 10, "order": "shuffled", "rows": None},
 "absent": [{"path": "close_signals.rrod_bp", "why": "not_until_15:30"}, {"path": "close_signals.r12_bp", "why": "not_until_15:30"},
   {"path": "options.charm_direction", "why": "constant_sign"}, {"path": "cross_asset.vx_futures", "why": "schwab_refuses_symbol"},
   {"path": "internals.tick_vold", "why": "same_day_wrong"}, {"path": "flow.ofi_1m", "why": "not_recorded"},
   {"path": "foreign.korea_taiwan", "why": "include_later"}, {"path": "flow.charm_signed", "why": "include_later"}],
}
# payload_id: canonical scene with precedent rows sorted by id, so s1 and s2 share it
canon_scene = dict(scene); canon_scene["precedents"] = dict(scene["precedents"], rows=[now] + sorted(cards, key=lambda c: c["id"]), order="by_id")
canon = json.dumps(canon_scene, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
payload_id = hashlib.sha256(canon.encode()).hexdigest()
shuf = list(cards); random.Random(int(payload_id[:12], 16)).shuffle(shuf)
scene["precedents"]["rows"] = [now] + shuf
rules = open(f'{D}/rules_draft2.txt', 'rb').read(); rules_sha = hashlib.sha256(rules).hexdigest()[:16]
ask = {"order": ["up", "flat", "down"],
       "horizons": {"h30": {"min": 30, "graded_on": "average_price", "edge_sig": 0.041},
                    "h60": {"min": 60, "graded_on": "average_price", "edge_sig": 0.064},
                    "to_close": {"min": 90, "graded_on": "official_close", "edge_sig": 0.143}}}
head = "Read this scene cold and reply with the JSON object only.\n\nSCENE:\n"
sent = head + json.dumps(scene, separators=(",", ":"), ensure_ascii=False) + "\n\nASK:\n" + json.dumps(ask, separators=(",", ":"))
prompt_sha = hashlib.sha256(sent.encode()).hexdigest()
contract = {"sent_to_claude": False, "schema": "spx_claude_payload/1.0.0", "era": "cr-1",
  "read_id": "live:2026-10-09T14:30:12.458122-04:00", "slot": "14:30", "source": "live", "cut_at": "2026-10-09T14:30:12-04:00",
  "built_lag_s": 241, "payload_id": "sha256:" + payload_id[:16], "prompt_sha256_s1": prompt_sha[:16], "rules_sha": rules_sha,
  "builder_sha": "unbuilt", "events_sha": "sha256 of calendar/events.json",
  "call": {"model": "claude-opus-5-5", "effort": "medium", "transport": "claude -p append mode, SNDK flags, no --json-schema",
    "cwd": "empty folder outside the repo", "canary": "no CLAUDE.md or memory attachment", "date_shown": True,
    "samples": {"s1": "up-first, cards shuffled", "s2": "down-first, cards reversed"}},
  "quality_flags": {"anchor_src": "native_morning", "anchor_vs_vix_x": 1.0, "unit_suspect": False, "code_source": "live", "flow_src": "agg_live"},
  "logged_never_sent": {"spot": 7813.01, "sigma_pts": 75.31, "edge_pts": {"h30": 3.12, "h60": 4.84, "to_close": 10.79},
    "code_top3": code_order[:3], "always_empty_feeds": ["$ADD", "$VOLD", "$VOLSPD"], "headline_ids": 81}}
rule_out = {"lives_in": "cached rulebook, same every read; shown here for review",
  "reply_schema": {
    "matched": [{"id": "<precedent id>", "share": "<int>", "same_on": ["<card field>"], "differs_on": "<card field>"}],
    "reasons": [{"field": "<scene path>", "pushes": "up|down|move|still", "for": "h30|h60|to_close|all"}],
    "against": {"field": "<scene path>", "pushes": "up|down|move|still", "for": "h30|h60|to_close|all"},
    "h30": {"<ask.order[0]>": "<int>", "<ask.order[1]>": "<int>", "<ask.order[2]>": "<int>",
            "up_size": ["<U1>", "<U2>", "<U3>"], "down_size": ["<D1>", "<D2>", "<D3>"]},
    "h60": "<same as h30; omitted when the window ends past the close>", "to_close": "<same as h30>"},
  "code_checks": ["shares sum to 100 (off by <=2 rescaled, else horizon malformed)", "up_size sums to up, down_size to down",
    "matched: 1-4 real card ids, not now, shares sum 100", "reasons: 2-4, each path resolves in SCENE and is not in absent",
    "anything failing is deleted, never rewritten"]}
def ll(p): return round(-math.log(p), 3)
def three(v, r=3):
    s = [x + 1 for x in v]; t = sum(s); p = [sum(s[4:]) / t, s[3] / t, sum(s[:3]) / t]
    p = [max(x, 0.02) for x in p]; t = sum(p); return [round(x / t, r) for x in p]
br3 = {h: three(BR[h]) for h in BR}
brx = {h: three(BR[h], 12) for h in BR}
sealed = {"stored_only": True, "sealed": "17:15 ET nightly", "join": "read_id + payload_id + rule_version",
  "bars_sha": "sha256 of the bars used", "close_src": "official_close",
  "h30": {"g_pts": 0.7, "edges": 0.22, "bucket": "F", "best_edges": 1.96, "worst_edges": -0.61},
  "h60": {"g_pts": 2.51, "edges": 0.52, "bucket": "F", "best_edges": 1.37, "worst_edges": -0.39},
  "to_close": {"g_pts": -1.47, "edges": -0.14, "bucket": "F"},
  "baselines_up_flat_down": {"base_rate": br3, "pool_v2": {"h30": [0.236, 0.616, 0.148], "h60": [0.186, 0.662, 0.152]}},
  "log_loss_eps_0.02": {"h30": {"base_rate": ll(brx['h30'][1]), "clock_odds": 0.425, "pool_v2": 0.484},
    "h60": {"base_rate": ll(brx['h60'][1]), "clock_odds_end_price_on_avg_label": 0.391, "pool_v2": 0.412},
    "to_close": {"base_rate": ll(brx['to_close'][1])}}}
doc = {"contract": contract, "scene": scene, "ask": ask, "rulebook_output": rule_out, "sealed_outcome": sealed}
json.dump(doc, open(f'{D}/mock_final.json', 'w'))
sc = json.dumps(scene, separators=(",", ":"), ensure_ascii=False)
pr = json.dumps(scene["precedents"], separators=(",", ":"))
print("payload_id", payload_id[:16], "prompt", prompt_sha[:16], "rules", rules_sha, "rules chars", len(rules))
print("sent chars", len(sent), "scene", len(sc), "precedents", len(pr), "ask", len(json.dumps(ask, separators=(",", ":"))))
print("est new tokens per call (SNDK fit 1131+0.548*chars):", round(1131 + 0.548 * len(sent)))
for k, v in scene.items():
    s = json.dumps(v, separators=(",", ":")); print(f"{k:14s} {len(s):5d} chars ~{round(len(s)/1.83):4d} tok")
print("base 3way", br3)
print("shuffled", [c['id'] for c in shuf], "code order", code_order)
