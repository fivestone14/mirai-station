#!/usr/bin/env python3
"""mirai-voice :: bench — measure the ears and the mouth before trusting them.

Phase-0 harness (plan: calm-rolling-mountain). Two jobs:
  stt — synthesize jargon-heavy test sentences with macOS `say`, transcribe
        them with Parakeet, print latency + the raw transcript so real
        mis-hearings seed jargon.py. `say` voices are not the user's voice;
        the numbers are a floor check, the mis-hearings are the harvest.
  tts — time Kokoro from cold load to first synthesized sentence.

First run downloads models into ~/.cache/huggingface (Parakeet ~1.2 GB,
Kokoro ~330 MB); subsequent runs are offline.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

# sentences chosen to stress the vocabulary, not the acoustics
SENTENCES = [
    "Paint me the picture on SNDK right now.",
    "Is the GEX wall at two ten holding or breaking?",
    "Where is the HVL and which side of the gamma flip are we on?",
    "What does the DEX say about dealer flow this morning?",
    "Why is the arrow silent when spot is pinned at the magnet?",
    "Give me the zero DTE picture and the expected move in sigma terms.",
    "Did the put wall at two oh five see any effort at the wall?",
    "How does vanna and charm decay shape the close today?",
    "Compare the call wall to VWAP and the prior close.",
    "Is MagP double prime still the top strike in the book?",
]


def _say_wav(text: str, out: Path) -> None:
    subprocess.run(
        ["say", "-o", str(out), "--data-format=LEI16@16000", text],
        check=True, capture_output=True)


def bench_stt() -> None:
    # array path (soundfile → get_logmel → generate): no ffmpeg, and it is the
    # SAME path ears.py uses on live PCM — the bench exercises production code.
    import mlx.core as mx
    import soundfile as sf
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    t0 = time.perf_counter()
    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    print(f"[stt] model warm in {time.perf_counter() - t0:.1f}s")

    tmp = Path(tempfile.mkdtemp(prefix="mirai-voice-bench-"))
    lat = []
    for i, s in enumerate(SENTENCES):
        wav = tmp / f"{i}.wav"
        _say_wav(s, wav)
        audio, sr = sf.read(wav, dtype="float32")
        assert sr == 16000, sr
        t0 = time.perf_counter()
        mel = get_logmel(mx.array(audio), model.preprocessor_config)
        result = model.generate(mel)[0]
        dt = time.perf_counter() - t0
        lat.append(dt)
        print(f"[stt] {dt * 1000:5.0f}ms | said: {s}")
        print(f"      {'':7} heard: {result.text}")
    lat.sort()
    print(f"[stt] median {lat[len(lat) // 2] * 1000:.0f}ms  "
          f"max {lat[-1] * 1000:.0f}ms  over {len(lat)} clips")


def bench_tts() -> None:
    import soundfile as sf
    from kokoro import KPipeline

    t0 = time.perf_counter()
    pipe = KPipeline(lang_code="a")  # American English
    print(f"[tts] pipeline warm in {time.perf_counter() - t0:.1f}s")

    text = "Dealers are long gamma and spot is pinned to the magnet at two ten."
    t0 = time.perf_counter()
    first = None
    tmp = Path(tempfile.mkdtemp(prefix="mirai-voice-bench-"))
    for _, _, audio in pipe(text, voice="af_heart"):
        if first is None:
            first = time.perf_counter() - t0
        sf.write(tmp / "tts.wav", audio, 24000)
    print(f"[tts] first audio {first * 1000:.0f}ms | total "
          f"{(time.perf_counter() - t0) * 1000:.0f}ms | wav at {tmp}/tts.wav")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("stt", "all"):
        bench_stt()
    if what in ("tts", "all"):
        bench_tts()
