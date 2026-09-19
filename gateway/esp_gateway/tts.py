"""Kokoro TTS client → PCM16 mono at the device's playback rate.

Kokoro returns a WAV (typically 24 kHz); we parse it, downmix to mono, and resample
to the device's requested rate so the firmware can write it straight to I2S.
"""

from __future__ import annotations

import logging
import asyncio

import httpx

from .audio import resample_i16, to_pcm_bytes, wav_to_pcm16

logger = logging.getLogger(__name__)


class KokoroTTS:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()

    async def synthesize(self, text: str, voice: str, dst_rate: int, *, speed: float = 1.0, pitch_semitones: float = 0.0) -> bytes:
        """Return PCM16 mono bytes at dst_rate for the given text."""
        text = text.strip()
        if not text:
            return b""
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "speed": speed,
        }
        r = await self._client.post(
            f"{self.base_url}/v1/audio/speech", json=payload, timeout=60.0
        )
        r.raise_for_status()
        samples, rate = wav_to_pcm16(r.content)
        if pitch_semitones:
            # Match the audition: raise pitch/formants, then restore the duration.
            # Async process keeps websocket capture and playback responsive.
            factor = 2 ** (pitch_semitones / 12)
            filters = f"asetrate={round(rate * factor)},aresample={rate},atempo={1 / factor:.6f}"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-v", "error", "-i", "pipe:0",
                "-af", filters, "-ar", str(dst_rate), "-ac", "1",
                "-f", "s16le", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                pcm, error = await asyncio.wait_for(proc.communicate(r.content), timeout=30)
                if proc.returncode:
                    raise RuntimeError(f"pet voice processing failed: {error.decode(errors='replace')[:200]}")
                return pcm
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        samples = resample_i16(samples, rate, dst_rate)
        return to_pcm_bytes(samples)

    async def aclose(self) -> None:
        await self._client.aclose()
