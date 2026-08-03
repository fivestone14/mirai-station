"""mirai-voice :: ears — raw PCM in, corrected transcript out.

Parakeet (parakeet-tdt-0.6b-v3 via MLX) on the array path — no ffmpeg, no
temp files: float32 mono 16 kHz → log-mel → generate. Bench 08-03: median
157 ms per utterance on this machine after a one-time ~2 s JIT warm-up.

Silero VAD is used only to TRIM silence and reject empty clips (push-to-talk
and browser-side endpointing decide when an utterance ends; Whisper-family
hallucination-on-silence applies less to Parakeet but an empty clip is still
a wasted model call). Fail-open: if VAD is unavailable, transcribe untrimmed.
"""
from __future__ import annotations

import os
import time

# offline after the phase-0 pre-fetch — same pattern as the right-eye embedder
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np

SAMPLE_RATE = 16_000
_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"


class Ears:
    def __init__(self) -> None:
        self._model = None
        self._vad = None

    def warm(self) -> float:
        """Load the model and JIT it on a dummy clip. Returns seconds spent."""
        t0 = time.perf_counter()
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        self._mx, self._get_logmel = mx, get_logmel
        self._model = from_pretrained(_MODEL_ID)
        try:
            from silero_vad import get_speech_timestamps, load_silero_vad
            self._vad = load_silero_vad()
            self._vad_ts = get_speech_timestamps
        except Exception:
            self._vad = None
        # JIT warm-up so the first real utterance doesn't pay ~2 s
        self.hear(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), trim=False)
        return time.perf_counter() - t0

    def _trim(self, audio: np.ndarray) -> np.ndarray | None:
        """Silence-trim via Silero. None = no speech at all."""
        if self._vad is None:
            return audio
        try:
            import torch
            stamps = self._vad_ts(torch.from_numpy(audio), self._vad,
                                  sampling_rate=SAMPLE_RATE)
        except Exception:
            return audio
        if not stamps:
            return None
        pad = SAMPLE_RATE // 10  # keep 100 ms shoulders
        lo = max(0, stamps[0]["start"] - pad)
        hi = min(len(audio), stamps[-1]["end"] + pad)
        return audio[lo:hi]

    def hear(self, audio: np.ndarray, trim: bool = True) -> tuple[str, float]:
        """float32 mono 16 kHz → (raw transcript, seconds). "" = no speech."""
        if self._model is None:
            self.warm()
        t0 = time.perf_counter()
        if trim:
            audio = self._trim(audio)
            if audio is None or len(audio) < SAMPLE_RATE // 10:
                return "", time.perf_counter() - t0
        mel = self._get_logmel(self._mx.array(audio),
                               self._model.preprocessor_config)
        text = self._model.generate(mel)[0].text.strip()
        return text, time.perf_counter() - t0


def pcm16_to_float32(raw: bytes) -> np.ndarray:
    """Browser/mic PCM16 little-endian mono → float32 in [-1, 1]."""
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
