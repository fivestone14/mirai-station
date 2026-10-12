"""baselines — normalcy memory: keys, robust z, trust labels, calendar gate."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lob_flow.baselines import (BaselineStore, DEFAULT_CALENDAR, FileCalendar,
                                day_samples, first_friday, key_of,
                                session_block, third_friday, tod_bin,
                                vix_band, vix_expiration_wednesday)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)


def test_session_blocks(cfg):
    assert session_block(9 * 60 + 29, cfg) is None          # premarket
    assert session_block(9 * 60 + 30, cfg) == "open"
    assert session_block(9 * 60 + 45, cfg) == "mid"
    assert session_block(15 * 60 + 44, cfg) == "mid"
    assert session_block(15 * 60 + 45, cfg) == "close"
    assert session_block(16 * 60, cfg) is None               # after close


def test_tod_bin_and_vix_band():
    assert tod_bin(NOW) == "1030"
    assert tod_bin(NOW.replace(minute=34)) == "1030"         # same 5-min slot
    assert vix_band(None) == "na"
    assert vix_band(12) == "low" and vix_band(18) == "mid" and vix_band(30) == "high"


def test_trust_label_progression(tmp_path, cfg, store_seeder):
    k = key_of("size", "d40_50", "mid", "1030", "na")
    cold = store_seeder(tmp_path / "b.json", cfg, NOW, {k: 100.0}, days=5)
    assert cold.label == "cold"
    assert cold.score(k, 1.0, NOW.date()) == {"z": None, "label": "cold", "breach": None}

    # percentile = confidence tier ONLY: no z, no alerts (min/max bands on a
    # thin history false-alarm ~12%/observation, so they are not wired)
    pct = store_seeder(tmp_path / "b2.json", cfg, NOW, {k: 100.0}, days=16)
    assert pct.label == "percentile"
    s = pct.score(k, 1.0, NOW.date())
    assert s["z"] is None and s["breach"] is None and s["label"] == "percentile"

    rob = store_seeder(tmp_path / "b3.json", cfg, NOW, {k: 100.0}, days=21)
    assert rob.label == "robust"
    assert rob.score(k, 100.0, NOW.date())["z"] == 0.0


def test_trust_label_is_per_key_not_per_store(tmp_path, cfg, store_seeder):
    """A 21-day store meeting a brand-new key (fresh VIX band / new slot)
    must treat THAT key as cold — the store's age is not the key's age."""
    k_old = key_of("size", "d40_50", "mid", "1030", "na")
    store = store_seeder(tmp_path / "b.json", cfg, NOW, {k_old: 100.0}, days=21)
    k_new = key_of("size", "d40_50", "mid", "1030", "high")   # unseen VIX band
    s = store.score(k_new, 1.0, NOW.date())
    assert s["label"] == "cold" and s["z"] is None


def test_quantum_floor_tames_frozen_histories(tmp_path, cfg, store_seeder):
    """21 identical tick-quantized days: a one-tick spread widening or a
    one-lot size wiggle must NOT read as a monster z."""
    ks = key_of("spread", "d40_50", "mid", "1030", "na")
    kz = key_of("size", "d40_50", "mid", "1030", "na")
    store = store_seeder(tmp_path / "b.json", cfg, NOW,
                         {ks: 0.15, kz: 20.0}, days=21)
    one_tick = store.score(ks, 0.20, NOW.date())["z"]
    one_lot = store.score(kz, 21.0, NOW.date())["z"]
    assert abs(one_tick) < cfg.spread_z_alert
    assert abs(one_lot) < cfg.size_z_alert


def test_robust_z_flags_collapse_not_normal_wiggle(tmp_path, cfg, store_seeder):
    """21 days around 100 with real day-to-day spread: 95 is nothing, 5 is a
    scatter-scale collapse."""
    k = key_of("size", "d40_50", "mid", "1030", "na")
    store = BaselineStore(tmp_path / "b.json", cfg)
    d, added, vals = NOW.date() - timedelta(days=1), 0, [90, 95, 100, 105, 110]
    while added < 21:
        if d.weekday() < 5:
            store.fold_day(d.isoformat(), {k: float(vals[added % 5])}, clean=True)
            added += 1
        d -= timedelta(days=1)
    z_small = store.score(k, 95.0, NOW.date())["z"]
    z_crash = store.score(k, 5.0, NOW.date())["z"]
    assert abs(z_small) < cfg.size_z_alert
    assert z_crash < -cfg.size_z_alert


def test_mad_floor_prevents_fake_infinity(tmp_path, cfg, store_seeder):
    k = key_of("size", "d40_50", "mid", "1030", "na")
    store = store_seeder(tmp_path / "b.json", cfg, NOW, {k: 100.0})  # zero spread
    z = store.score(k, 99.0, NOW.date())["z"]
    assert z is not None and abs(z) < 1e5                    # floored, not inf


def test_fold_refuses_event_days_and_duplicates(tmp_path, cfg):
    store = BaselineStore(tmp_path / "b.json", cfg)
    assert store.fold_day("2026-07-06", {"k": 1.0}, clean=True)
    assert not store.fold_day("2026-07-06", {"k": 2.0}, clean=True)   # dup
    assert not store.fold_day("2026-07-29", {"k": 9.0}, clean=False)  # FOMC
    assert store.clean_days == 1


def test_window_trims_to_config(tmp_path, cfg):
    store = BaselineStore(tmp_path / "b.json", cfg)
    d, added = NOW.date(), 0
    while added < 30:
        if d.weekday() < 5:
            store.fold_day(d.isoformat(), {"k": float(added)}, clean=True)
            added += 1
        d -= timedelta(days=1)
    assert store.clean_days == cfg.baseline_days             # 21, not 30


def test_persistence_roundtrip(tmp_path, cfg, store_seeder):
    k = key_of("size", "d40_50", "mid", "1030", "na")
    store_seeder(tmp_path / "b.json", cfg, NOW, {k: 100.0})
    reloaded = BaselineStore(tmp_path / "b.json", cfg)
    assert reloaded.label == "robust"
    assert reloaded.score(k, 100.0, NOW.date())["z"] == 0.0


def test_day_samples_collects_and_caps():
    rows = [{"key": "a", "value": float(i)} for i in range(30)]
    rows += [{"key": "b", "value": 7.0},
             {"key": None, "value": 9.0}, {"key": "c", "value": None}]
    out = day_samples(rows, cap=12)
    assert len(out["a"]) == 12 and out["b"] == [7.0] and "c" not in out
    assert out["a"][0] == 0.0                               # evenly thinned


# --- calendar gate -----------------------------------------------------------


def test_friday_rules():
    assert first_friday(2026, 7) == date(2026, 7, 3)
    assert third_friday(2026, 7) == date(2026, 7, 17)


def test_calendar_seeds_and_blocks_fomc_window(tmp_path):
    cal = FileCalendar(tmp_path / "calendar.json")
    assert (tmp_path / "calendar.json").exists()             # seeded
    fomc = datetime(2026, 7, 29, 14, 10, tzinfo=ET)
    b = cal.block(fomc)
    assert b.blocked and b.kind == "FOMC"
    assert not cal.block(fomc.replace(hour=11)).blocked      # outside the window


def test_calendar_blocks_cpi_and_nfp_mornings(tmp_path):
    cal = FileCalendar(tmp_path / "calendar.json")
    cpi = datetime(2026, 7, 14, 8, 45, tzinfo=ET)
    assert cal.block(cpi).kind == "CPI"
    nfp = datetime(2026, 8, 7, 8, 30, tzinfo=ET)             # first Friday Aug
    assert cal.block(nfp).kind == "NFP"


def test_clean_day_rules(tmp_path):
    cal = FileCalendar(tmp_path / "calendar.json")
    assert not cal.is_clean_day(date(2026, 7, 29))           # FOMC
    assert not cal.is_clean_day(date(2026, 7, 17))           # OPEX (computed)
    assert not cal.is_clean_day(date(2026, 8, 7))            # NFP (computed)
    assert cal.is_clean_day(date(2026, 7, 7))                # boring Tuesday


def test_opex_dirty_but_not_blocked_intraday(tmp_path):
    cal = FileCalendar(tmp_path / "calendar.json")
    opex_noon = datetime(2026, 7, 17, 12, 0, tzinfo=ET)
    assert not cal.block(opex_noon).blocked


def test_default_calendar_marks_unverified():
    unverified = [e for e in DEFAULT_CALENDAR["events"] if not e.get("verified")]
    assert unverified, "pattern-based dates must stay flagged for the refresh"


def test_calendar_fails_closed_beyond_coverage(tmp_path):
    """Past the seeded horizon the calendar can't know the event days — every
    day there is DIRTY (never folded) until the file is refreshed."""
    cal = FileCalendar(tmp_path / "calendar.json")
    assert not cal.is_clean_day(date(2027, 3, 9))            # unknown territory


# --- semantic-fix regressions (domain-expert review, 07-04) ---------------------


def test_cold_vix_band_borrows_neighbor(tmp_path, cfg, store_seeder):
    """The first VIX>25 regime must not mute the storm sensor: a cold 'high'
    band borrows 'mid' at inflated MAD instead of holding no opinion."""
    k_mid = key_of("size", "d40_50", "mid", "1030", "mid")
    store = store_seeder(tmp_path / "b.json", cfg, NOW, {k_mid: 100.0}, days=21)
    k_high = key_of("size", "d40_50", "mid", "1030", "high")
    s = store.score(k_high, 5.0, NOW.date())
    assert s["label"] == "borrowed" and s["z"] is not None and s["z"] < 0
    # inflation: the borrowed z is milder than the native-band z would be
    native = store.score(k_mid, 5.0, NOW.date())
    assert abs(s["z"]) < abs(native["z"])


def test_fomc_block_covers_the_presser(tmp_path):
    """The violent half of a Fed day is the 14:30-15:30 press conference."""
    cal = FileCalendar(tmp_path / "calendar.json")
    presser = datetime(2026, 7, 29, 15, 15, tzinfo=ET)
    assert cal.block(presser).kind == "FOMC"
    after = datetime(2026, 7, 29, 15, 45, tzinfo=ET)
    assert not cal.block(after).blocked


def test_vix_expiration_and_quarter_roll_are_dirty(tmp_path):
    cal = FileCalendar(tmp_path / "calendar.json")
    assert vix_expiration_wednesday(date(2026, 7, 22)) == date(2026, 7, 22)
    assert not cal.is_clean_day(date(2026, 7, 22))          # VIX settlement day
    open_block = datetime(2026, 7, 22, 9, 45, tzinfo=ET)
    assert cal.block(open_block).kind == "VIXEXP"           # SOQ morning quiet
    assert not cal.is_clean_day(date(2026, 9, 30))          # quarter-end roll
    assert not cal.block(datetime(2026, 9, 30, 12, 0, tzinfo=ET)).blocked


def test_reachability_audit(tmp_path, cfg):
    """The audit that catches a structurally dead alarm: dispersed history ->
    finite max |z| reported."""
    k = key_of("size", "d40_50", "mid", "1030", "na")
    store = BaselineStore(tmp_path / "b.json", cfg)
    d, added = NOW.date() - timedelta(days=1), 0
    while added < 21:
        if d.weekday() < 5:
            store.fold_day(d.isoformat(), {k: [80.0, 100.0, 120.0]}, clean=True)
            added += 1
        d -= timedelta(days=1)
    r = store.reachability(NOW.date())
    assert r is not None and 1.0 < r < 50.0


def test_bin_level_dispersion_makes_threshold_reachable(tmp_path, cfg):
    """21 days x 3 bin samples with realistic wiggle: a collapse to near-zero
    must clear the -4 bar (it could NOT clear -8 on daily medians)."""
    k = key_of("size", "d40_50", "mid", "1030", "na")
    store = BaselineStore(tmp_path / "b.json", cfg)
    d, added = NOW.date() - timedelta(days=1), 0
    while added < 21:
        if d.weekday() < 5:
            store.fold_day(d.isoformat(), {k: [85.0, 100.0, 115.0]}, clean=True)
            added += 1
        d -= timedelta(days=1)
    z = store.score(k, 2.0, NOW.date())["z"]
    assert z is not None and z <= -cfg.size_z_alert
