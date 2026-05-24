"""Audio helpers: WAV (de)serialization and PCM resampling.

All gateway audio is PCM signed-16-bit little-endian, mono. The device sends/receives
raw PCM frames; Kokoro returns WAV that we parse, downmix to mono, and resample.
"""

from __future__ import annotations

import io
import wave

import numpy as np


def pcm16_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Wrap raw PCM16 mono bytes in a WAV container (for Groq STT upload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(data: bytes) -> tuple[np.ndarray, int]:
    """Parse a 16-bit PCM WAV → (mono int16 samples, sample_rate).

    Raises ValueError on non-16-bit WAV (Kokoro returns 16-bit PCM).
    """
    with wave.open(io.BytesIO(data), "rb") as w:
        nch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got sampwidth={width}")
    samples = np.frombuffer(frames, dtype="<i2")
    if nch > 1:
        samples = samples.reshape(-1, nch).mean(axis=1).astype(np.int16)
    return samples, rate


def resample_i16(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample of mono int16 samples."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.int16, copy=False)
    n_dst = int(round(len(samples) * dst_rate / src_rate))
    if n_dst <= 0:
        return np.zeros(0, dtype=np.int16)
    x_src = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_dst = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    out = np.interp(x_dst, x_src, samples.astype(np.float32))
    return np.clip(out, -32768, 32767).astype(np.int16)


def to_pcm_bytes(samples: np.ndarray) -> bytes:
    """int16 samples → little-endian PCM bytes."""
    return samples.astype("<i2", copy=False).tobytes()
