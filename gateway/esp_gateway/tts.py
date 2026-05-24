"""Kokoro TTS client → PCM16 mono at the device's playback rate.

Kokoro returns a WAV (typically 24 kHz); we parse it, downmix to mono, and resample
to the device's requested rate so the firmware can write it straight to I2S.
"""

from __future__ import annotations

import logging

import httpx

from .audio import resample_i16, to_pcm_bytes, wav_to_pcm16

logger = logging.getLogger(__name__)


class KokoroTTS:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()

    async def synthesize(self, text: str, voice: str, dst_rate: int) -> bytes:
        """Return PCM16 mono bytes at dst_rate for the given text."""
        text = text.strip()
        if not text:
            return b""
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "speed": 1.0,
        }
        r = await self._client.post(
            f"{self.base_url}/v1/audio/speech", json=payload, timeout=60.0
        )
        r.raise_for_status()
        samples, rate = wav_to_pcm16(r.content)
        samples = resample_i16(samples, rate, dst_rate)
        return to_pcm_bytes(samples)

    async def aclose(self) -> None:
        await self._client.aclose()
