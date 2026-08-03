"""mirai-voice :: mouth — sentences in, PCM16 audio out.

Kokoro-82M (voice: af_heart) at 24 kHz. Bench 08-03: ~14 s one-time pipeline
boot, ~1.1 s to first audio for a full sentence — the sidecar synthesizes
sentence-by-sentence off the model's stream, so speech starts while the rest
of the answer is still being written.

Fail-open to macOS `say` (rendered to PCM, resampled tag carried in the
result) so the desk still talks when Kokoro can't load. The squawk path uses
`say` directly and never touches this module.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KOKORO_RATE = 24_000
_VOICE = "af_heart"

# strip what a TTS engine should never see (markdown remnants, code ticks)
_UNSPEAKABLE = re.compile(r"[*_`#>\[\]{}|\\]")


@dataclass
class Speech:
    pcm16: bytes          # little-endian mono
    rate: int
    seconds: float        # synthesis wall time
    engine: str           # "kokoro" | "say"


class Mouth:
    def __init__(self) -> None:
        self._pipe = None
        self._engine = "kokoro"

    def warm(self) -> float:
        t0 = time.perf_counter()
        try:
            from kokoro import KPipeline
            self._pipe = KPipeline(lang_code="a")
            # first synthesis JITs; do it now, not on the first real sentence
            self.speak("Ready.")
        except Exception:
            self._pipe, self._engine = None, "say"
        return time.perf_counter() - t0

    def speak(self, text: str) -> Speech:
        text = _UNSPEAKABLE.sub(" ", text).strip()
        if not text:
            return Speech(b"", KOKORO_RATE, 0.0, self._engine)
        t0 = time.perf_counter()
        if self._pipe is not None:
            try:
                chunks = [audio for _, _, audio in
                          self._pipe(text, voice=_VOICE)]
                audio = np.concatenate([np.asarray(c) for c in chunks])
                pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                return Speech(pcm, KOKORO_RATE,
                              time.perf_counter() - t0, "kokoro")
            except Exception:
                self._pipe, self._engine = None, "say"  # degrade for the session
        return self._say(text, t0)

    @staticmethod
    def _say(text: str, t0: float) -> Speech:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav = Path(f.name)
        try:
            subprocess.run(
                ["say", "-o", str(wav), "--data-format=LEI16@22050", text],
                check=True, capture_output=True, timeout=30)
            import soundfile as sf
            audio, rate = sf.read(wav, dtype="int16")
            return Speech(audio.astype("<i2").tobytes(), rate,
                          time.perf_counter() - t0, "say")
        finally:
            wav.unlink(missing_ok=True)
