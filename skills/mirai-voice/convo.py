"""mirai-voice :: convo — the persistent spoken conversation with Claude.

One `ClaudeSDKClient` session per trading day, held open so a turn costs no
CLI spawn (the repo's one-shot `claude -p` calls run 7–100 s wall; a held
session streams first tokens in well under a second). Auth is the Claude
subscription via the CLI credential chain — this process scrubs
ANTHROPIC_API_KEY so a stray exported key can never silently take over
billing (env.sh:22-24 is the policy; the SDK honors the same precedence).

Scene discipline: every user turn MAY open with a <scene> block — the same
unbiased snapshot the reading model gets (reused verbatim from
sndk_read.build_scene, never reimplemented). Anti-bloat rule: the full scene
rides only when it is stale (>120 s) or materially changed (spot >0.25σ,
regime flip, new top magnet strike); otherwise a one-liner. The doctrine
never repeats — it lives in the appended system prompt.

Tool surface: exactly sndk_read's grant — the RAG CLI plus WebSearch,
everything else denied (see sndk_read.py:159-166 for the precedent and
_NO_TOOLS for the deny list). Default permission mode, never bypass.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# a stray exported key would outrank the subscription profile — scrub before
# the SDK ever spawns the CLI
os.environ.pop("ANTHROPIC_API_KEY", None)

_SKILL_DIR = Path(__file__).resolve().parent
_ROOT = _SKILL_DIR.parent.parent
_SNDK_PRO = str(_SKILL_DIR.parent / "sndk-pro")
if _SNDK_PRO not in sys.path:
    sys.path.insert(0, _SNDK_PRO)

import sndk_read as _sr                # noqa: E402 — scene builder, reused not rebuilt
import atomic_io                       # noqa: E402 — via the left-eye path sndk_read inserts

from doctrine import VOICE_DOCTRINE    # noqa: E402

PINNED_MODEL = "claude-sonnet-5"       # exact id, never an alias (drift protection)
TURN_TIMEOUT_S = 100.0                 # mirrors sndk_read.CALL_TIMEOUT_S
CONNECT_TIMEOUT_S = 30.0               # session connect/resume — a wedged resume
                                       # must fail loud, never hang the lock (08-03)
LOCK_WAIT_S = 20.0                     # a stuck prior turn speaks, never starves
MAX_TURNS = 8                          # tool-loop bound inside one user turn
_SCENE_FRESH_S = 120.0
_SCENE_SPOT_SIGMA = 0.25

_RAG_CMD = f"{sys.executable} {Path(_SNDK_PRO) / 'sndk_rag.py'}"
_ALLOWED_TOOLS = (f"Bash({_RAG_CMD}:*)", "WebSearch")
_NO_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
             "Grep", "WebFetch", "Task", "Agent", "TodoWrite", "ExitPlanMode",
             "BashOutput", "KillShell", "SlashCommand", "Skill")

# transient-failure detector, kept in sync with runtime/watch/claude_cli.py:27
_RATE_LIMIT_RE = re.compile(
    r"(rate.?limit|429|too\s+many\s+requests|usage\s+limit|quota|please\s+wait)",
    re.IGNORECASE)

_STATE_DIR = Path(os.environ.get("MIRAI_STATE_DIR",
                                 _ROOT / "state")) / "voice"
_SESSION_FILE = _STATE_DIR / "session.json"

SPOKEN_TIMEOUT = "That one timed out on me. Ask again."
SPOKEN_RATELIMIT = "I've hit the usage limit. Give me a few minutes."
SPOKEN_ERROR = "Something broke on my end. It's logged. Try once more."
SPOKEN_BUSY = "Still chewing on the last one. Give me a second, then ask again."


# ---------------------------------------------------------------------------
# sentence chunking — deltas in, speakable sentences out
# ---------------------------------------------------------------------------
_BOUNDARY = re.compile(r"[.!?;](?=\s|$)")
_DIGIT_DOT = re.compile(r"\d\.\d")


class SentenceChunker:
    """Whole sentences only. The old first-chunk-at-a-comma rule bought ~a
    second of perceived latency and paid for it in choppy delivery — comma
    fragments synthesized as separate utterances with dead air at the seams
    (user report 08-03: "talks then breaks then talks"). Flow beats snap:
    the speak-ahead queue in voice_server now hides the latency instead."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._cut_at()
            if cut is None:
                break
            out.append(self._buf[:cut].strip())
            self._buf = self._buf[cut:].lstrip()
        return [s for s in out if s]

    def _cut_at(self) -> int | None:
        for m in _BOUNDARY.finditer(self._buf):
            end = m.end()
            if end < 25:
                continue
            around = self._buf[max(0, m.start() - 1):m.start() + 2]
            if _DIGIT_DOT.search(around):
                continue
            return end
        if len(self._buf) >= 250:
            sp = self._buf.rfind(" ", 0, 250)
            return sp if sp > 0 else 250
        return None

    def flush(self) -> str | None:
        s, self._buf = self._buf.strip(), ""
        return s or None


# ---------------------------------------------------------------------------
# the scene block — reused from the reader, with the reader's own line added
# ---------------------------------------------------------------------------
def _load_rows(day: str) -> list[dict]:
    rows = [r for r in _sr._read_jsonl(_sr._diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"
            and not (r.get("meta") or {}).get("forced")]
    return rows


def _latest_read(day: str) -> dict | None:
    reads = [r for r in _sr._read_jsonl(_sr._reads_dir() / f"{day}.jsonl")
             if r.get("era") == _sr.ERA]
    return reads[-1] if reads else None


def build_voice_scene(now: datetime | None = None) -> dict | None:
    """The reader's exact scene + a reader_line block. None when no tape."""
    now = now or datetime.now(_sr._ET)
    day = now.date().isoformat()
    rows = _load_rows(day)
    if not rows:
        return None
    row = _sr.with_path(rows[-1], rows)
    band = _sr.magnet_band(row)
    frozen = _sr.frozen_fields(rows, now)
    # obs-3: the voice hands the model the same scene the reader does, frame
    # included — its own charter says verbatim. Same anchor, same divergence
    # as the payload tab: no wake here, so `why_this_read` is absent.
    _reads = [r for r in _sr._read_jsonl(_sr._reads_dir() / f"{day}.jsonl")
              if r.get("era") == _sr.ERA]
    _lc = next((r for r in reversed(_reads)
                if r.get("wall_s") is not None), None)
    scene = _sr.build_scene(row, band, frozen, rows, now,
                            since_last_read=_sr.frame_since_last_read(
                                row, rows, _lc, None, False, now))
    read = _latest_read(day)
    if read:
        r = read.get("reading") or {}
        # obs-1. `line` and `vector` are gone. Left as they were, this handed
        # the voice model a scene whose reader_line was `{"arrow": "up"}` and
        # nothing else — a bare DETERMINISTIC direction, labelled as another
        # model's opinion, which the voice doctrine then tells it to quote and
        # attribute. It would have spoken a forecast the reader never made,
        # which is precisely the artefact obs-1 exists to delete.
        # obs-2. `say`, `quiet_because` and `notable` are all gone, so `says`
        # was always None and stripped by the prune below, and `found_unusual`
        # was a constant zero — the voice model was told to attribute a reading
        # that reached it empty, and would have said "the reader has seen
        # nothing unusual" on a read that named three levels.
        points = r.get("points") or []
        reader = {
            "says": r.get("read") or None,
            "levels": [p.get("level") for p in points if p.get("level") is not None] or None,
            "watching": len(points) or None,
            "quiet": True if r.get("quiet") else None,
            "age_min": read.get("reading_age_min"),
        }
        if read.get("paused"):
            reader["paused"] = True
        scene["reader_line"] = {k: v for k, v in reader.items()
                               if v is not None}
    return scene


class _SceneGate:
    """Full scene only when stale or materially moved; else a one-liner."""

    def __init__(self) -> None:
        self._t = 0.0
        self._spot: float | None = None
        self._sig: dict = {}

    def block(self, now: datetime) -> str:
        scene = build_voice_scene(now)
        stamp = now.strftime("%H:%M ET")
        if scene is None:
            return (f'<scene ts="{stamp}">no SNDK diary rows yet today — '
                    f'the tape has not started</scene>')
        # sr-7 key names. This gate fails SILENTLY when it misses — a None
        # here does not raise, it just makes every turn look unchanged — so
        # these four reads move in lockstep with build_scene, and
        # test_convo_scene_gate pins them against a real scene.
        spot = (scene.get("price") or {}).get("live_spot")
        sig = {
            "regime": (scene.get("regime") or {}).get("gamma_sign"),
            "magnet": ((scene.get("magnet") or {}).get("top_strikes")
                       or [{}])[0].get("strike"),
        }
        one_sigma = (scene.get("scale") or {}).get("one_sigma_dollars") or 0
        moved = (self._spot is not None and spot is not None and one_sigma
                 and abs(spot - self._spot) / one_sigma >= _SCENE_SPOT_SIGMA)
        fresh = (time.monotonic() - self._t) < _SCENE_FRESH_S
        if fresh and not moved and sig == self._sig:
            return (f'<scene ts="{stamp}">unchanged since last turn; '
                    f'spot {spot}</scene>')
        self._t, self._spot, self._sig = time.monotonic(), spot, sig
        return (f'<scene ts="{stamp}">\n'
                + json.dumps(scene, default=str) + "\n</scene>")


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------
class VoiceSession:
    """Lazy, day-scoped, restart-resumable. `ask()` yields:
    ("sentence", str) · ("tool", str) · ("error", spoken) · ("done", dict)."""

    def __init__(self) -> None:
        self._client = None
        self._day: str | None = None
        self._gate = _SceneGate()
        self._lock = asyncio.Lock()
        _STATE_DIR.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ----------------------------------------------------------
    def _options(self, resume: str | None):
        from claude_agent_sdk import ClaudeAgentOptions
        return ClaudeAgentOptions(
            model=PINNED_MODEL,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": VOICE_DOCTRINE},
            allowed_tools=list(_ALLOWED_TOOLS),
            disallowed_tools=list(_NO_TOOLS),
            mcp_servers={},
            strict_mcp_config=True,
            max_turns=MAX_TURNS,
            cwd=str(_ROOT),
            include_partial_messages=True,
            resume=resume,
        )

    async def _ensure(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient
        today = datetime.now(_sr._ET).date().isoformat()
        if self._client is not None and self._day == today:
            return
        if self._client is not None:
            await self._disconnect()
        resume = None
        try:
            saved = json.loads(_SESSION_FILE.read_text())
            if saved.get("date") == today:
                resume = saved.get("session_id")
        except Exception:
            pass
        # Connect is bounded, and a wedged RESUME is not fatal: burn the
        # session file and start fresh rather than hang the whole desk.
        # (08-03: a service restart orphaned the CLI child; the new process's
        # resume then hung forever inside connect, silently starving every
        # later question behind the turn lock.)
        try:
            self._client = ClaudeSDKClient(options=self._options(resume))
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                await self._client.connect()
        except Exception as e:
            await self._disconnect()
            if resume is None:
                raise
            self._log({"event": "resume_failed_going_fresh", "detail": repr(e)})
            _SESSION_FILE.unlink(missing_ok=True)
            self._client = ClaudeSDKClient(options=self._options(None))
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                await self._client.connect()
        self._day = today

    async def _disconnect(self) -> None:
        try:
            await self._client.disconnect()
        except Exception:
            pass
        self._client = None

    async def reset(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._disconnect()
            _SESSION_FILE.unlink(missing_ok=True)
            self._day = None

    async def close(self) -> None:
        """Shutdown path: hang up the CLI child cleanly, KEEP the session
        file — the next boot resumes the day's conversation."""
        if self._client is not None:
            await self._disconnect()

    # -- one spoken turn ----------------------------------------------------
    async def ask(self, transcript: str):
        """Yields ("sentence"|"tool"|"error"|"done", payload).

        Discipline learned 08-03: the lock is acquired with a deadline (a
        stuck prior turn SPEAKS instead of starving you), and abandonment is
        first-class — when the consumer aclose()es us mid-stream (barge-in),
        the GeneratorExit path interrupts the model and drains the transport
        WHILE STILL HOLDING THE LOCK, so the next turn meets a clean session,
        never a wedged one."""
        try:
            async with asyncio.timeout(LOCK_WAIT_S):
                await self._lock.acquire()
        except TimeoutError:
            yield ("error", SPOKEN_BUSY)
            self._log({"event": "lock_starved", "heard": transcript})
            yield ("done", {"lock_starved": True})
            return
        try:
            try:
                await self._ensure()
            except Exception as e:
                yield ("error", SPOKEN_ERROR)
                self._log({"event": "connect_error", "detail": repr(e)})
                yield ("done", {"connect_error": True})
                return

            now = datetime.now(_sr._ET)
            prompt = self._gate.block(now) + "\n\n" + transcript
            chunker = SentenceChunker()
            spoken: list[str] = []
            info: dict = {}
            mid_stream = False
            try:
                async with asyncio.timeout(TURN_TIMEOUT_S):
                    await self._client.query(prompt)
                    mid_stream = True
                    async for msg in self._client.receive_response():
                        for kind, payload in self._digest(msg, chunker, info):
                            if kind == "sentence":
                                spoken.append(payload)
                            yield (kind, payload)
                    mid_stream = False
                tail = chunker.flush()
                if tail:
                    spoken.append(tail)
                    yield ("sentence", tail)
            except GeneratorExit:
                # consumer walked away (barge-in / socket died) — clean the
                # session up under the lock, then let aclose complete
                if mid_stream:
                    await self._interrupt_drain()
                self._log({"ts": now.isoformat(), "heard": transcript,
                           "spoke": spoken, "abandoned": True, **info})
                raise
            except TimeoutError:
                await self._interrupt_drain()
                yield ("error", SPOKEN_TIMEOUT)
                info["timeout"] = True
            except Exception as e:
                detail = repr(e)
                if _RATE_LIMIT_RE.search(detail):
                    yield ("error", SPOKEN_RATELIMIT)
                else:
                    yield ("error", SPOKEN_ERROR)
                    await self._disconnect()   # unknown state → rebuild lazily
                info["exception"] = detail

            self._log({"ts": now.isoformat(), "heard": transcript,
                       "spoke": spoken, **info})
            yield ("done", info)
        finally:
            self._lock.release()

    def _digest(self, msg, chunker: SentenceChunker, info: dict):
        """One SDK message → zero or more (kind, payload) events."""
        from claude_agent_sdk import (AssistantMessage, ResultMessage,
                                      StreamEvent, ToolUseBlock)
        out: list[tuple[str, str | dict]] = []
        if isinstance(msg, StreamEvent):
            ev = msg.event or {}
            if ev.get("type") == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta":
                    for s in chunker.feed(delta.get("text", "")):
                        out.append(("sentence", s))
            elif ev.get("type") == "content_block_start":
                block = ev.get("content_block") or {}
                if block.get("type") == "tool_use":
                    out.append(("tool", block.get("name", "tool")))
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    info.setdefault("tools", []).append(block.name)
        elif isinstance(msg, ResultMessage):
            info["session_id"] = msg.session_id
            info["is_error"] = msg.is_error
            if msg.total_cost_usd is not None:
                info["cost_usd"] = msg.total_cost_usd
            if msg.is_error and msg.result and \
                    _RATE_LIMIT_RE.search(str(msg.result)):
                out.append(("error", SPOKEN_RATELIMIT))
            self._persist(msg.session_id)
        return out

    async def interrupt_now(self) -> None:
        """Fire-and-forget interrupt from OUTSIDE the turn (barge-in): a pure
        control write, safe alongside the streaming reader. The drain stays
        with the generator that holds the lock — never two readers."""
        try:
            if self._client is not None:
                await self._client.interrupt()
        except Exception:
            pass

    async def _interrupt_drain(self) -> None:
        try:
            await self._client.interrupt()
            async for _ in self._client.receive_response():
                pass
        except Exception:
            await self._disconnect()

    # -- bookkeeping --------------------------------------------------------
    def _persist(self, session_id: str | None) -> None:
        if not session_id or not self._day:
            return
        try:
            _SESSION_FILE.write_text(json.dumps(
                {"date": self._day, "session_id": session_id}))
        except Exception:
            pass

    def _log(self, rec: dict) -> None:
        try:
            day = datetime.now(_sr._ET).date().isoformat()
            atomic_io.append_jsonl(_STATE_DIR / f"convo-{day}.jsonl", rec)
        except Exception:
            pass
