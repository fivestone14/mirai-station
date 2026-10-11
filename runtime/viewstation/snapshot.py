"""Mirai Viewstation — the JSON builders behind the data routes.

The live spot (one Schwab quote behind a short cache, and the SNDK spot tape it
feeds), a past session replayed through the Watchtower's own grader, the SNDK
payload rebuilt through the reader's own code and kept warm by a refresher
thread, one recorded session's firing times for the Pipeline Architecture
modal, and an inventory of the SNDK memory store. Nothing here re-derives or
fakes data: every builder reads the live state files and calls the same skill
functions the jobs call.

No third-party deps. Designed to run under the mirai-station venv (so the
imported skill modules find their own dependencies).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    ET = None

# --- locate the plugin + make the skill modules importable -------------------
PLUGIN_ROOT = Path(__file__).resolve().parents[2]          # .../mirai-station
SKILL_DIR = PLUGIN_ROOT / "skills" / "mirai-left-eye"

_RUNTIME = str(PLUGIN_ROOT / "runtime")                     # station_paths names every store this server reads
if _RUNTIME not in sys.path:
    sys.path.append(_RUNTIME)
import station_paths  # noqa: E402

STATE_DIR = station_paths.state_root()

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import reversion_lens as rev      # noqa: E402


# --- helpers -----------------------------------------------------------------
def _now_et() -> datetime:
    return datetime.now(ET) if ET else datetime.now()


def _state_dir() -> Path:
    """The SNDK state folder, resolved per call exactly as sndk_read resolves
    it. The payload route reads through sndk_read, so a reader here that fixed
    its folder at import could put two folders on one page."""
    env = os.environ.get("MIRAI_STATE_DIR")
    return Path(env) if env else STATE_DIR


"""=== LIVE SPOT (the streaming price) ================================================

The map's price used to be a SCAN SNAPSHOT: the tablet polled every 4s but only ever
re-read the diary, and the diary is written by the left-eye scanner — launchd asks for
60s, but an RTH scan overruns that and launchd DROPS the overlapping fire. Measured on
2026-07-13: a new price every 2 min at best, 4.1 min median, 12.5 min at worst. The UI
eased smoothly between those points, so a 12-minute-old print still LOOKED live.

This is the fix: one cheap Schwab quote (`lefteye_fetcher.live_spot` — a single-quote
call, never the chain), served behind a short cache so N tablets and a fast poll can
never multiply into N×poll upstream calls.

Rules it must obey:
  * NEVER fabricate. Upstream returns None (dead bearer, market closed, network) →
    `spot: None` and the tablet keeps showing the diary's scan price, exactly as before.
  * The GRAVITY (magnet, walls, flip, regime) still comes from the diary. Only the PRICE
    streams. A live price hung on stale gravity is honest; a fabricated one is not.
"""
_SPOT_CACHE: dict = {"ts": 0.0, "val": None, "src": None}
_SPOT_TTL_S = 2.0          # ≥ the tablet's poll gap, so the quote is shared, not multiplied
_SPOT_LOCK = __import__("threading").Lock()
_SPOT_FETCH_LOCK = __import__("threading").Lock()   # single-flight for the upstream call (sweep 08-11)

# --- SNDK spot tape (08-11) --------------------------------------------------
# The SNDK tab's 5s /api/spot poll already buys a fresh Schwab quote (2s TTL
# above); persisting it is FREE — zero additional upstream calls, no sampler
# process. Only fresh (non-cached) fetches for a tape ticker append, only
# inside a pure-clock RTH window (never a market-hours API call — the tape may
# not add load to earn its gate), so the file is exactly "what a watcher
# already paid for". Unwatched stretches keep the hunter's 2-min diary spots.
_TAPE_TICKERS = ("SNDK",)
_TAPE_DIR = station_paths.SNDK_LAST_PRICES_BY_QUOTE.folder(STATE_DIR)
_TAPE_MIN_GAP_S = 4.0      # append floor: a burst of pollers still writes ≤1 row/4s
_TAPE_LAST: dict = {"ts": 0.0}


def _tape_append(ticker: str, px: float, mono_ts: float) -> None:
    """Best-effort by contract: the tape is a by-product, so any failure here
    (full disk, bad perms) must never dent the /api/spot response."""
    if ticker not in _TAPE_TICKERS or mono_ts - _TAPE_LAST["ts"] < _TAPE_MIN_GAP_S:
        return
    try:
        now = _now_et()
        if now.weekday() >= 5:
            return
        hm = now.hour * 60 + now.minute
        if not (9 * 60 + 30 <= hm <= 16 * 60):
            return
        import atomic_io               # SKILL_DIR is on sys.path (module header)
        atomic_io.append_jsonl(_TAPE_DIR / station_paths.SNDK_LAST_PRICES_BY_QUOTE.file_name(day=now.date().isoformat()),
                               {"ts": now.isoformat(), "spot": round(float(px), 4),
                                "src": "schwab_quote"})
        _TAPE_LAST["ts"] = mono_ts
    except Exception:
        pass


def live_spot(ticker: str = "SPX") -> dict:
    """The current index print for the map. Cached `_SPOT_TTL_S` so a room full of
    tablets polling every 3s still costs upstream ~1 quote every 2s. Fail-open: any
    error is a null spot, never a stale value dressed up as fresh."""
    import time as _t
    now = _t.time()
    with _SPOT_LOCK:
        c = _SPOT_CACHE
        if c["val"] is not None and (now - c["ts"]) < _SPOT_TTL_S and c.get("tk") == ticker:
            return {"ticker": ticker, "spot": c["val"], "source": c["src"],
                    "age_s": round(now - c["ts"], 2), "cached": True}
    px = None
    # SINGLE-FLIGHT (20-agent sweep 08-11): the upstream call rides its own lock so
    # N tablets missing the cache at the same instant serialize into ONE Schwab hit
    # — the "N tablets never multiply into N× upstream calls" guarantee, made true.
    # Late arrivals re-check the cache after acquiring; the winner's fill serves them.
    with _SPOT_FETCH_LOCK:
        with _SPOT_LOCK:
            c = _SPOT_CACHE
            if c["val"] is not None and (_t.time() - c["ts"]) < _SPOT_TTL_S and c.get("tk") == ticker:
                return {"ticker": ticker, "spot": c["val"], "source": c["src"],
                        "age_s": round(_t.time() - c["ts"], 2), "cached": True}
        try:
            import lefteye_fetcher
            px = lefteye_fetcher.live_spot(ticker)
        except Exception:
            px = None
        px = float(px) if isinstance(px, (int, float)) and px > 0 else None
        with _SPOT_LOCK:
            if px is not None:
                _SPOT_CACHE.update({"ts": now, "val": px, "src": "schwab_quote", "tk": ticker})
    if px is not None:
        _tape_append(ticker, px, now)      # outside the lock — file IO never blocks a poll
    return {"ticker": ticker, "spot": px,
            "source": "schwab_quote" if px is not None else None,
            "age_s": 0.0, "cached": False,
            "ts": datetime.now(ET).isoformat() if ET else None}


def replay_day(day: str) -> dict:
    """Authoritative Watchtower grading for one past session — the data behind the
    tablet's Replay tab. The SWEEP LEDGER (gex_polarity_ab._grade_watchtower_sweep,
    2026-07-17 — the single scoreboard that replaced the trade + scene ledgers) is
    computed on-demand with the exact grader the nightly card uses — nothing
    re-derives outcomes here. Read-only.

    `fires` keeps the per-fire excursion vector from the canonical resolver so the
    chart can still plot mfe/mae/move_60m, but each fire's `outcome` is ITS parent
    sweep call's verdict (win/loss/mixed/pending) — the chips are sweep-authoritative.
    """
    import gex_polarity_ab as ab  # same SKILL_DIR, already on sys.path
    rows = ab._read_rows(day)
    _bars_cache: dict = {}       # one bar fetch shared by BOTH graders (grade_day's pattern)

    def _bars(tk):
        if tk not in _bars_cache:
            _bars_cache[tk] = ab._default_bars(tk, day) or []
        return _bars_cache[tk]

    sweep = ab._grade_watchtower_sweep(rows, _bars) or {}
    calls = sweep.get("calls") or []

    def _parent_verdict(ts):
        # calls are t0-sorted and a dir-flip fire sits exactly AT the next call's
        # t_open, so the LAST call opened at/before the fire is its parent
        v = None
        for c in calls:
            if c["t_open"] <= ts:
                v = c["verdict"]
            else:
                break
        return v

    trades, _pending = rev._resolve_beta_trades(rows, bars_lookup=_bars,
                                                signal_key="watchtower")
    pred_at = {r.get("ts"): (r.get("watchtower") or {}).get("magnitude_sigma")
               for r in rows if isinstance(r.get("watchtower"), dict)}
    fires = [{"ts": t.get("ts"), "dir": t.get("dir"),
              "outcome": _parent_verdict(t.get("ts") or ""),
              "mfe_sigma": t.get("mfe_sigma"), "mae_sigma": t.get("mae_sigma"),
              "move_60m": t.get("move_60m"), "ttr_min": t.get("ttr_min"), "r": t.get("r"),
              "pred_magnitude_sigma": pred_at.get(t.get("ts"))}
             for t in trades]
    return {"day": day, "sweep": sweep, "fires": fires}


# --- SNDK payload (08-21) — the exact scene JSON the reader hands the model ------
# The "SNDK Payload" tab shows ONLY what Claude is fed: the scene dict that
# skills/sndk-pro/sndk_read.build_scene assembles from the newest diary row,
# rebuilt here through the SAME functions (never a copy of the logic), plus the
# 12-word user-message wrapper it rides in. The cached doctrine is deliberately
# not returned — the tab's job is the varying payload, not the rulebook.
#
# `now` matters: clock.*, data_sources.*.age_min, wall/frozen ages and the
# 30-minute windows are all measured against it. While the newest row is fresh
# the scene is built against the wall clock, exactly as a live read would; once
# it is not, the scene is built as of that row's own timestamp, so the tab shows
# the last scene the reader COULD have built rather than a 17-hour-old book with
# minutes_to_close 0. The response says which (`as_of`).
#
# 2026-08-30 — THE CUTOVER IS THE BOOK'S CEILING, not a round number. It was 10
# minutes, chosen before sr-7 gave build_scene a freshness gate that DELETES an
# aged-out block. Those two rules met in the middle and made the tab
# non-monotonic: a 1-minute-old row rendered 4,363 chars, a 5-minute-old row
# rendered 1,871 (eight evidence blocks deleted, because the book behind it had
# crossed six minutes), and an 11-minute-old row rendered the full 4,363 again
# because the as-of build reset every age to ~0. The payload was most complete
# when the data was oldest, every trading day from about 16:02 to 16:09 ET.
#
# Pinning the cutover to the book's own ceiling closes it: while the book would
# survive the gate we build live and nothing is dropped; the moment it would
# not, we switch to the as-of build and show the whole scene with data_sources
# saying how old it is. The tab never renders a gutted scene: a reader of the
# scene alone, without `freshness_rules` or `data_sources`, would take a block
# the gate deleted for a wall that was never measured.
_SNDK_PRO_DIR = PLUGIN_ROOT / "skills" / "sndk-pro"


def _side_packet(R, row: dict, build_now: datetime,
                 day: Optional[str]) -> Optional[dict]:
    """The side payload as the reader would build it at `build_now`. Returns
    None rather than raising: the tab must still render the scene when the
    packet cannot be built, and an empty panel is a smaller failure than a
    500 on the whole payload route."""
    try:
        import sndk_side as S
        return S.build_side(
            R.minute_bars(day), day, build_now,
            levels=S.levels_from_row(row),
            levels_as_of_bar=S.bar_index(R._book_asof(row) or R._ts(row), day),
            profile=S.segment_profile(S.prior_sessions(day)))
    except Exception:
        return None


# ---- the payload cache -------------------------------------------------------
# Building the payload is the slowest thing the Payload tab waits for (about two
# seconds: the scene is rebuilt from the whole day's diary through the reader's own
# code). It only changes when a diary row, a reading, a bar or a side packet lands,
# so it is built once and served from memory, and a refresher thread rebuilds it in
# the background when one of those files changes or a minute has passed (the scene
# carries a clock, so it must never be older than the tab's own poll). A request
# is served whatever is cached, even a copy a few seconds behind a row that just
# landed, and waits for a build only when nothing has been built yet. Callers that
# pass their own `now` (tests, replays) always build fresh and never touch the cache.
_PAY_CACHE: dict = {"key": None, "val": None, "built": 0.0}
_PAY_LOCK = threading.Lock()
_PAY_REFRESH_S = 60.0        # a rebuild at least this often keeps the scene's clock within a minute
_PAY_POLL_S = 5.0            # how often the refresher looks for a new file
_PAY_THREAD: dict = {"started": False}
_PAY_SAID: dict = {}         # a failed build's message -> when the refresher last printed it
_PAY_SAY_EVERY_S = 600.0     # the same failure is printed once per this, not once per poll


def _payload_key() -> tuple:
    """What the payload depends on: the newest diary file and, for that day, the
    readings and the bars (the side packet is built from the bars). Sizes and mtimes, no contents read."""
    state = _state_dir()
    diary = station_paths.SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN.folder(state)
    files = sorted(diary.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")) if diary.is_dir() else []
    if not files:
        return ("no diary",)
    newest = files[-1]
    day = newest.stem
    key = [str(state), day]
    for path in (newest, station_paths.SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN.path(state, day=day),
                 station_paths.SNDK_PRICE_BARS_BY_MINUTE.path(state, day=day)):
        try:
            st = path.stat()
            key.append((path.name, st.st_size, st.st_mtime_ns))
        except OSError:
            key.append((path.name, None, None))
    return tuple(key)


def _payload_stale() -> bool:
    c = _PAY_CACHE
    return c["val"] is None or c["key"] != _payload_key() or (time.time() - c["built"]) >= _PAY_REFRESH_S


def _rebuild_payload() -> dict:
    with _PAY_LOCK:
        key = _payload_key()
        if _PAY_CACHE["val"] is not None and _PAY_CACHE["key"] == key and (time.time() - _PAY_CACHE["built"]) < _PAY_REFRESH_S:
            return _PAY_CACHE["val"]          # another thread built it while this one waited for the lock
        val = _build_payload(_now_et())
        _PAY_CACHE.update({"key": key, "val": val, "built": time.time()})
        return val


def _payload_refresher() -> None:
    while True:
        time.sleep(_PAY_POLL_S)
        try:
            if _payload_stale():
                _rebuild_payload()
        except Exception as exc:  # the refresher must outlive one bad build; the next poll tries again
            _say_build_failed(f"{type(exc).__name__}: {exc}")


def _say_build_failed(msg: str) -> None:
    """One line per distinct failure, and that line again only after _PAY_SAY_EVERY_S. A build
    that fails on every poll would otherwise write the same line twelve times a minute, which
    is the noise _Station.handle_error exists to keep out of the launchd log."""
    now = time.monotonic()
    if msg in _PAY_SAID and now - _PAY_SAID[msg] < _PAY_SAY_EVERY_S:
        return
    _PAY_SAID[msg] = now
    print(f"sndk payload refresher: build failed: {msg}", file=sys.stderr, flush=True)


def start_payload_refresher() -> None:
    """Start the background rebuilder once. server.main() calls it at start-up; a test that
    exercises the route in-process never does, so no thread outlives a test."""
    with _PAY_LOCK:
        if _PAY_THREAD["started"]:
            return
        _PAY_THREAD["started"] = True
    threading.Thread(target=_payload_refresher, name="sndk-payload-refresher", daemon=True).start()


def sndk_payload(now: Optional[datetime] = None) -> dict:
    """The SNDK payload the Payload tab shows. With `now` given: built fresh, for tests and replays. Without:
    served from the cache the refresher keeps warm, built on the spot only when empty."""
    if now is not None:
        return _build_payload(now)
    c = dict(_PAY_CACHE)                      # one snapshot, so the age belongs to the value served
    if c["val"] is None:
        _rebuild_payload()
        c = dict(_PAY_CACHE)
    out = dict(c["val"])
    out["cache_age_s"] = round(time.time() - c["built"], 1)
    return out


def _build_payload(now: datetime) -> dict:
    if str(_SNDK_PRO_DIR) not in sys.path:
        sys.path.insert(0, str(_SNDK_PRO_DIR))
    import sndk_read as R   # lazy: the tab is rarely open; keeps server start light
    diary = R._diary_dir()
    files = sorted(diary.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl"))
    rows: list = []
    day = None
    for path in reversed(files):                      # newest session with live rows
        rows = [r for r in R._read_jsonl(path)
                if r.get("ticker") == "SNDK" and not (r.get("meta") or {}).get("forced")]
        if rows:
            day = path.stem
            break
    if not rows:
        return {"error": "no SNDK diary rows yet", "scene": None}
    row = rows[-1]
    t_row = R._ts(row)
    # the BOOK's age decides, not the scan's: the book is always the older of
    # the two, and it is the one the freshness gate measures.
    book_age = R._age_min(R._book_asof(row), now)
    live = book_age is not None and book_age <= R.MAX_BOOK_AGE_MIN
    build_now = now if live else (t_row or now)
    rw = R.with_path(row, rows)
    band = R.magnet_band(rw)
    frozen = R.frozen_fields(rows, build_now)
    # obs-3: the payload tab's charter is the exact scene the reader hands the
    # model, so it carries the same since-last-read frame — anchored on the
    # last row that spent a call, exactly as read_once anchors it. One declared
    # divergence: no wake fires here, so `why_this_read` is absent.
    # The era filter keeps two CONTRACTS from pooling, which is right when the
    # question is "how did the reader do". It is wrong here, where the question
    # is only "when did it last speak, and what did it say" — a sentence is a
    # sentence whichever doctrine wrote it. Filtering strictly meant that on the
    # day the era flipped, every row on disk was excluded, `last_call` came back
    # None, and the tab announced `first_read_of_session: true` at 15:58 on a
    # day with 26 recorded readings. Prefer the current era; fall back to the
    # whole day rather than to nothing.
    all_reads = R._read_jsonl(R._reads_dir() / f"{day}.jsonl")
    reads = [r for r in all_reads if r.get("era") == R.active_era()] or all_reads
    last_call = next((r for r in reversed(reads)
                      if r.get("wall_s") is not None), None)
    said = next((r for r in reversed(reads)
                 if (r.get("reading") or {}).get("read")), None)
    scene_v1 = R.build_scene(rw, band, frozen, rows, build_now,
                             since_last_read=R.frame_since_last_read(
                                 rw, rows, last_call, None, False, build_now,
                                 bars=R.minute_bars(day)))
    # strikes-1 (2026-09-05): the tab shows what the reader hands the model,
    # and that is now the Strikes Payload, built from the same v1 the reader
    # builds it from. The Scene Payload rides beside it as LEGACY, still built
    # every scan for the wake gate and the memory, no longer sent; the Gate
    # Payload (the old magnet and walls) rides as its own document. Both are
    # labelled so a reader can tell current from deprecated at a glance.
    mode = R.payload_mode()
    scene, legacy, gate_payload = scene_v1, None, None
    payload_label = "Scene Payload v5"
    if mode == "strikes":
        try:
            import sndk_board as board
            frame = (scene_v1.get("context") or {}).get("since_last_read")
            scene, _ = board.build_scene_v2(
                rw, rows, build_now, since_last_read=frame,
                last_read_ts=(R._ts(last_call) if last_call else None),
                bars=R.minute_bars(day), v1=scene_v1,
                strikes_sent_before=(last_call or {}).get("strikes_sent"),
                sent_before_without_volume=bool((last_call or {}).get("strikes_sent_without_volume")),
                said_row=said,
                # strikes-6: the readings `day` grades, filtered by era the way
                # read_once filters them — not the whole-day fallback above, or on an
                # era-flip day the tab would show claims the reader never sends
                calls_today=[r for r in all_reads
                             if r.get("era") == R.active_era() and r.get("wall_s") is not None])
            gate_payload = board.legacy(rw, rows, build_now, v1=scene_v1)
            payload_label = "Strikes Payload v1"
            v1_text = json.dumps(scene_v1, default=str)
            legacy = {"label": "Scene Payload v5", "status": "deprecated",
                      "era": R.LEGACY_ERA, "scene": scene_v1, "chars": len(v1_text),
                      "sent_to_model": False,
                      "note": "still built every scan for the wake gate and the memory slice; the model no longer sees it"}
        except Exception as exc:   # the tab must show SOMETHING true, never a blank
            scene, legacy, gate_payload = scene_v1, None, None
            payload_label = f"Scene Payload v5 (strikes payload failed: {type(exc).__name__})"
    text = (board.prompt_v2(scene).split("SCENE:\n", 1)[1] if (mode == "strikes" and legacy is not None)
            else json.dumps(scene, default=str))
    return {
        "payload": ("strikes" if legacy is not None else "scene"),
        "payload_label": payload_label,
        "legacy": legacy,
        "gate_payload": gate_payload,
        "session": day,
        "row_ts": row.get("ts"),
        "built_at": build_now.isoformat(),
        "as_of": "live" if live else "last scan",
        "era": R.active_era(),
        "model": R.PINNED_MODEL,
        "scans_today": len(rows),
        "scene": scene,
        "scene_chars": len(text),
        # side-1: the bar-anchored packet, rebuilt live through its own builder
        # exactly as the scene is, so the tab shows what the reader would put on
        # the row rather than a copy that can drift from it. A sibling of the
        # scene and never a child: user_prompt and scene_chars below are pinned
        # to the scene alone, and this document is not sent to the model at all.
        "side": _side_packet(R, row, build_now, day),
        "user_prompt": "Read this scene cold and reply with the JSON object only.\n\nSCENE:\n" + text,
        # what must be true before this scene is worth a model call. Read off the
        # reader's OWN constants rather than retyped into the page: a tab that
        # quotes a threshold the reader stopped using is the doctrine-drifts-from-
        # code trap, and this build has already been caught by it six times.
        "gates": {
            "min_gap_min": R.MIN_GAP_MIN,
            "daily_cap": R.DAILY_CALL_CAP,
            "stale_book_min": R.STALE_BOOK_MIN,
            "heartbeat_min": R.HEARTBEAT_MIN,
            # wk-1 renamed and re-scoped these: the gate now measures spot
            # travel against the state at the last SPEECH, reads implied vol off
            # a 5-book median rather than the raw print, and needs a discrete
            # flip to hold for two consecutive books before it counts.
            # strikes-3: plain travel is counted in typical minutes (the median
            # one-minute high-to-low over the last half hour), not in sigma
            "move_minutes": R.MOVE_MINUTES,
            "iv_pp": R.WAKE_IV_PP,
            "flip_sigma": R.WAKE_FLIP_SIGMA,
            "confirm_books": R.WAKE_CONFIRM_BOOKS,
            # 09-02: the two numbers the gate card used to carry as literals
            "iv_median_books": R.WAKE_IV_MEDIAN_BOOKS,
            "flip_cross_sigma": R.WAKE_FLIP_CROSS_SIGMA,
            # sr-7 freshness ceilings, read off the reader's own constants for
            # the same reason as every gate above: the tab must not be able to
            # quote a ceiling the reader has stopped enforcing.
            "max_book_age_min": R.MAX_BOOK_AGE_MIN,
            # how far the minute price log may trail the clock before the board
            # warns the model — and before the SNDK tab warns a person. Read off
            # the reader so the two alarms cannot drift apart.
            "bars_stale_min": R.BAR_RECORD_STALE_MIN,
        },
    }


# --- SNDK pipeline events (09-05) — one session's firing times ------------------
# The Pipeline Architecture modal replays a recorded session: every scan, every
# distinct option book, every completed minute bar and every read row, as
# minutes since the open, all derived from the rows' own timestamps. Nothing
# is typed in and nothing is interpolated; a day that lacks a file says so.
def _minutes_since_open(ts: str) -> Optional[float]:
    try:
        t = datetime.fromisoformat(ts).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None
    return round((t.hour - 9) * 60 + t.minute - 30 + t.second / 60.0, 2)


def _jsonl_rows(path: Path) -> list:
    out: list = []
    if not path.exists():
        return out
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue   # a torn line is skipped, never fabricated
            # and so is a line that parses to anything but a row, as
            # sndk_read._read_jsonl skips it: every caller reads rows with .get
            if isinstance(r, dict):
                out.append(r)
    return out


def pipeline_days(limit: int = 30) -> list:
    """Days that have a diary and minute bars, newest first."""
    state = _state_dir()
    have_bars = {p.stem for p in station_paths.SNDK_PRICE_BARS_BY_MINUTE.folder(state).glob("[0-9]*-[0-9]*-[0-9]*.jsonl")}
    days = sorted((p.stem for p in station_paths.SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN.folder(state).glob("[0-9]*-[0-9]*-[0-9]*.jsonl")
                   if p.stem in have_bars), reverse=True)
    return days[:limit]


def pipeline_events(day: str) -> dict:
    state = _state_dir()
    absent: list = []
    scans, books, seen = [], [], None
    for r in _jsonl_rows(station_paths.SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN.path(state, day=day)):
        if r.get("ticker") != "SNDK" or (r.get("meta") or {}).get("forced"):
            continue
        m = _minutes_since_open(r.get("ts") or "")
        if m is None:
            continue
        scans.append(m)
        ba = (r.get("meta") or {}).get("book_asof")
        key = ba or r.get("ts")
        if key != seen:
            seen = key
            books.append(_minutes_since_open(ba) if ba else m)
    if not scans:
        absent.append("no diary rows for the day")
    bars = [m for b in _jsonl_rows(station_paths.SNDK_PRICE_BARS_BY_MINUTE.path(state, day=day))
            if (m := _minutes_since_open(b.get("ts") or "")) is not None]
    if not bars:
        absent.append("no minute bars on disk for the day")
    reads = []
    for r in _jsonl_rows(station_paths.SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN.path(state, day=day)):
        m = _minutes_since_open(r.get("ts") or "")
        if m is None:
            continue
        spoke = r.get("model") not in (None, "None", "")
        wall = r.get("wall_s")
        reads.append({"m": m, "wake": r.get("wake") or "quiet", "spoke": spoke,
                      "wall": (float(wall) if spoke and wall not in (None, "None") else None),
                      "quiet": (bool(r.get("quiet")) if spoke else None),
                      "err": (r.get("error") not in (None, "None")) if spoke else False,
                      "era": r.get("era"), "payload": r.get("payload")})
    if not reads:
        absent.append("no read rows for the day")
    return {"day": day, "scans": scans, "books": books, "bars": bars,
            "reads": reads, "absent": absent}


# --- SNDK memory overview (08-21) — what the model's history tool can reach -----
# Read-only inventory of the SNDK reader's memory store (sndk_rag today): the per-read
# slices (Face A metadata + the model's own sentence), the rolled-up day
# summaries, and the standing terrain. Counts and dates only — the records
# themselves come back through the real CLI (server._memory_query), so the
# Memory view shows exactly what the model would be handed.
def sndk_memory_overview() -> dict:
    state = _state_dir()                 # where that CLI finds it, per call
    sl_dir = station_paths.SNDK_OPTIONS_READER_NOTES_BY_READ.folder(state)
    sp = station_paths.SNDK_OPTIONS_READER_SESSION_SUMMARIES_BY_DAY.path(state)
    tp = station_paths.SNDK_OPTIONS_READER_TERRAIN_STATE.path(state)
    rag = sp.parent                      # the store the Memory view names
    days = []
    if sl_dir.exists():
        for p in sorted(sl_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")):
            rows = []
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("kind") == "slice":
                        rows.append(r)
            except OSError:
                continue
            if not rows:
                continue
            # obs-1: the histogram counts what each read FOUND, not which way it
            # leaned. Reading `meta.vector` — a field obs-1 stopped writing —
            # produced three zeros on every day and a bar chart of nothing.
            # 09-02: a forecast-era slice (it carries `vector`, never `quiet`)
            # answered a different question and is counted apart — the Sep-1
            # audit found 390 of them filed as "quiet".
            vec = {"unusual": 0, "quiet": 0, "dropped": 0, "legacy": 0}
            for r in rows:
                m = r.get("meta") or {}
                vec["legacy" if ("vector" in m and "quiet" not in m) else
                    "unusual" if m.get("notable_count") else
                    "dropped" if m.get("abstain") == "forced" else "quiet"] += 1
            days.append({"date": p.stem, "n": len(rows),
                         "first": (rows[0].get("meta") or {}).get("time"),
                         "last": (rows[-1].get("meta") or {}).get("time"),
                         "vectors": vec,
                         "last_line": (rows[-1].get("narrative") or "")[:160]})
    summaries = {"n": 0, "last": None, "first": None, "rag_v": None}
    if sp.exists():
        try:
            dates = []
            vers = set()
            for line in sp.read_text().splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") == "day_summary":
                    dates.append(r.get("date"))
                    vers.add(r.get("rag_v"))
            dates = sorted(d for d in dates if d)
            summaries = {"n": len(dates), "first": dates[0] if dates else None,
                         "last": dates[-1] if dates else None,
                         "rag_v": sorted(v for v in vers if v is not None)}
        except OSError:
            pass
    terrain = None
    if tp.exists():
        try:
            t = json.loads(tp.read_text())
            terrain = {"built": t.get("built"), "sessions": t.get("sessions"),
                       "rag_v": t.get("rag_v"), "narrative": t.get("narrative")}
        except (OSError, json.JSONDecodeError, ValueError):
            terrain = {"error": "unreadable"}
    return {"store": str(rag), "days": days, "summaries": summaries, "terrain": terrain,
            "slices_total": sum(d["n"] for d in days)}
