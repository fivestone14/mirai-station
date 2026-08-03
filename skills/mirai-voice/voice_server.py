#!/usr/bin/env python3
"""mirai-voice :: voice_server — the sidecar the SNDK tab talks to.

WebSocket on MIRAI_VOICE_PORT (default 8788), binding 0.0.0.0 like the
viewstation — LAN-open by design, no auth; the perimeter is the LAN
(runtime/viewstation/README.md documents the stance). Kill switch:
MIRAI_VOICE_DISABLE=1 (checked here AND in run-voice.sh — defense in depth).

Protocol (design: plan calm-rolling-mountain):
  browser → server   JSON {type: start|stop|cancel|reset}
                     binary = mic PCM16 mono 16 kHz frames
  server → browser   JSON {type: ready|partial|final|sentence|tool|
                           audio_start|audio_end|turn_end|timeout|error}
                     binary = TTS PCM16 mono frames (rate in audio_start),
                     bracketed by audio_start/audio_end {utt}

Turn-taking is server-side: Silero VADIterator endpoints the open mic
(min_silence 500 ms). Speech detected while the agent is talking = barge-in:
queued speech is dropped, the model turn is interrupted, the floor is yours.

GPU-bound models never run on the event loop: Parakeet and Kokoro each get a
single-worker executor (serialized — one GPU, no contention).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SKILL_DIR))

import numpy as np
import websockets

from convo import VoiceSession
from ears import Ears, pcm16_to_float32, SAMPLE_RATE
from jargon import correct
from mouth import Mouth

PORT = int(os.environ.get("MIRAI_VOICE_PORT", "8788"))
IDLE_OFF_S = 180.0          # open mic auto-off after this much silence
_VAD_CHUNK = 512            # samples per VAD window (silero contract @16k)
_MAX_UTT_S = 45.0           # hard cap on one utterance

_ears = Ears()
_mouth = Mouth()
_session = VoiceSession()
_stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
_tts_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")


def _j(**kw) -> str:
    return json.dumps(kw)


class Conn:
    """One browser connection: mic state, endpointing, turn orchestration."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.mic_on = False
        self.buf = np.zeros(0, dtype=np.float32)
        self.speech_from: int | None = None
        self.last_voice = time.monotonic()
        self.utt = 0
        self.turn_task: asyncio.Task | None = None
        self.cancel_flag = asyncio.Event()
        from silero_vad import VADIterator, load_silero_vad
        self._vad = VADIterator(load_silero_vad(), sampling_rate=SAMPLE_RATE,
                                min_silence_duration_ms=500, speech_pad_ms=100)
        self._pending = np.zeros(0, dtype=np.float32)

    # -- mic ingest ---------------------------------------------------------
    async def feed(self, raw: bytes) -> None:
        if not self.mic_on:
            return
        self._pending = np.concatenate([self._pending, pcm16_to_float32(raw)])
        while len(self._pending) >= _VAD_CHUNK:
            chunk, self._pending = (self._pending[:_VAD_CHUNK],
                                    self._pending[_VAD_CHUNK:])
            await self._vad_step(chunk)
        # auto-off: an open mic that hears nobody shuts itself
        if (time.monotonic() - self.last_voice) > IDLE_OFF_S:
            await self._mic_off(timeout=True)

    async def _vad_step(self, chunk: np.ndarray) -> None:
        self.buf = np.concatenate([self.buf, chunk])
        cap = int(_MAX_UTT_S * SAMPLE_RATE)
        if len(self.buf) > cap:                    # ring: keep the tail
            drop = len(self.buf) - cap
            self.buf = self.buf[drop:]
            if self.speech_from is not None:
                self.speech_from = max(0, self.speech_from - drop)
        import torch
        try:
            ev = self._vad(torch.from_numpy(chunk))
        except Exception:
            ev = None
        if ev and "start" in ev:
            self.last_voice = time.monotonic()
            self.speech_from = max(0, len(self.buf) - _VAD_CHUNK
                                   - SAMPLE_RATE // 10)
            if self.turn_task and not self.turn_task.done():
                await self._barge_in()
        elif ev and "end" in ev:
            self.last_voice = time.monotonic()
            if self.speech_from is None:
                return
            utt_audio = self.buf[self.speech_from:]
            self.buf = np.zeros(0, dtype=np.float32)
            self.speech_from = None
            await self._utterance(utt_audio)

    # -- one utterance ------------------------------------------------------
    async def _utterance(self, audio: np.ndarray) -> None:
        loop = asyncio.get_running_loop()
        raw, dt = await loop.run_in_executor(_stt_pool, _ears.hear, audio)
        if not raw:
            return
        text, hits = correct(raw)
        await self.ws.send(_j(type="final", text=text, stt_ms=int(dt * 1000),
                              fixed=hits))
        self.cancel_flag = asyncio.Event()
        self.turn_task = asyncio.create_task(self._turn(text))

    async def _turn(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        cancel = self.cancel_flag
        try:
            async for kind, payload in _session.ask(text):
                if cancel.is_set():
                    break
                if kind in ("sentence", "error"):
                    self.utt += 1
                    u = self.utt
                    await self.ws.send(_j(type="sentence", utt=u,
                                          text=str(payload),
                                          err=(kind == "error")))
                    sp = await loop.run_in_executor(
                        _tts_pool, _mouth.speak, str(payload))
                    if cancel.is_set():
                        break
                    if sp.pcm16:
                        await self.ws.send(_j(type="audio_start", utt=u,
                                              rate=sp.rate))
                        await self.ws.send(sp.pcm16)
                        await self.ws.send(_j(type="audio_end", utt=u,
                                              cancelled=False))
                elif kind == "tool":
                    await self.ws.send(_j(type="tool", name=str(payload)))
                elif kind == "done":
                    await self.ws.send(_j(type="turn_end",
                                          **{k: v for k, v in
                                             (payload or {}).items()
                                             if k in ("cost_usd", "tools")}))
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            try:
                await self.ws.send(_j(type="error", text=repr(e)))
            except Exception:
                pass

    async def _barge_in(self) -> None:
        self.cancel_flag.set()
        try:
            await self.ws.send(_j(type="audio_end", utt=self.utt,
                                  cancelled=True))
        except Exception:
            pass
        try:
            await _session._interrupt_drain()
        except Exception:
            pass

    async def _mic_off(self, timeout: bool = False) -> None:
        self.mic_on = False
        self.buf = np.zeros(0, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self.speech_from = None
        try:
            self._vad.reset_states()
        except Exception:
            pass
        if timeout:
            await self.ws.send(_j(type="timeout"))

    # -- control ------------------------------------------------------------
    async def control(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "start":
            self.mic_on = True
            self.last_voice = time.monotonic()
            try:
                self._vad.reset_states()
            except Exception:
                pass
        elif t == "stop":
            await self._mic_off()
        elif t == "cancel":
            if self.turn_task and not self.turn_task.done():
                await self._barge_in()
        elif t == "reset":
            await _session.reset()
            await self.ws.send(_j(type="ready", reset=True))


async def _handler(ws) -> None:
    peer = getattr(ws, "remote_address", ("?",))[0]
    print(f"mirai-voice :: connect from {peer}")
    conn = Conn(ws)
    await ws.send(_j(type="ready", engine=_mouth._engine))
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                await conn.feed(msg)
            else:
                try:
                    m = json.loads(msg)
                    print(f"mirai-voice :: [{peer}] {m.get('type')}")
                    await conn.control(m)
                except json.JSONDecodeError:
                    pass
    except websockets.ConnectionClosed:
        pass
    finally:
        print(f"mirai-voice :: disconnect {peer}")
        if conn.turn_task and not conn.turn_task.done():
            conn.cancel_flag.set()


async def main() -> None:
    if os.environ.get("MIRAI_VOICE_DISABLE") == "1":
        print("mirai-voice :: disabled (MIRAI_VOICE_DISABLE=1)")
        return
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_stt_pool, _ears.warm)
    await loop.run_in_executor(_tts_pool, _mouth.warm)
    print(f"mirai-voice :: models warm in {time.monotonic() - t0:.1f}s "
          f"(mouth={_mouth._engine})")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with websockets.serve(_handler, "0.0.0.0", PORT, max_size=2 ** 22):
        print(f"mirai-voice :: listening on :{PORT}")
        await stop.wait()
    print("mirai-voice :: stopped")


if __name__ == "__main__":
    asyncio.run(main())
