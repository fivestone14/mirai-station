#!/usr/bin/env python3
"""mirai-voice :: repl — prove the whole loop in a terminal, no browser.

    repl.py            type questions, hear Kokoro answers
    repl.py --mute     type questions, read answers (no TTS)
    repl.py --mic      hold Enter-to-talk: record until Enter again, then
                       ears → jargon → convo → mouth (the full pipeline)

Ctrl-C or "q" exits. Every turn is logged to state/voice/convo-{date}.jsonl
by convo itself.
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mute", action="store_true", help="no TTS")
    ap.add_argument("--mic", action="store_true", help="speak instead of type")
    args = ap.parse_args()

    from convo import VoiceSession
    session = VoiceSession()

    mouth = None
    if not args.mute:
        from mouth import Mouth
        mouth = Mouth()
        print("warming mouth…", flush=True)
        print(f"  mouth ready in {mouth.warm():.1f}s ({mouth._engine})")

    ears = None
    if args.mic:
        from ears import Ears
        ears = Ears()
        print("warming ears…", flush=True)
        print(f"  ears ready in {ears.warm():.1f}s")

    def _play(pcm: bytes, rate: int) -> None:
        import numpy as np
        import sounddevice as sd
        sd.play(np.frombuffer(pcm, dtype="<i2"), rate, blocking=True)

    def _record() -> "np.ndarray":
        import numpy as np
        import sounddevice as sd
        print("recording — Enter to stop…", flush=True)
        chunks: list[np.ndarray] = []
        stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                                callback=lambda d, *_: chunks.append(d.copy()))
        with stream:
            input()
        return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, "f4")

    print("mirai-voice repl — q to quit")
    while True:
        try:
            if args.mic:
                input("Enter to talk…")
                audio = _record()
                raw, dt = ears.hear(audio)
                if not raw:
                    print("  (heard nothing)")
                    continue
                from jargon import correct
                text, hits = correct(raw)
                print(f"you ({dt * 1000:.0f}ms{', fixed: ' + ','.join(hits) if hits else ''}): {text}")
            else:
                text = input("you> ").strip()
            if not text or text.lower() == "q":
                break
        except (EOFError, KeyboardInterrupt):
            break

        async for kind, payload in session.ask(text):
            if kind == "sentence":
                print(f"  mirai: {payload}")
                if mouth is not None:
                    sp = mouth.speak(str(payload))
                    if sp.pcm16:
                        _play(sp.pcm16, sp.rate)
            elif kind == "tool":
                print(f"  [tool: {payload}]")
            elif kind == "error":
                print(f"  [error spoken: {payload}]")
                if mouth is not None:
                    sp = mouth.speak(str(payload))
                    if sp.pcm16:
                        _play(sp.pcm16, sp.rate)
            elif kind == "done":
                d = payload or {}
                bits = [f"cost ${d['cost_usd']:.4f}" if "cost_usd" in d else "",
                        f"tools {d['tools']}" if d.get("tools") else ""]
                line = " · ".join(b for b in bits if b)
                if line:
                    print(f"  ({line})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
