"""Speech-to-text backends.

GroqSTT matches the butler LiveKit agent's choice (whisper-large-v3-turbo, cloud,
low CPU). NullSTT is the text-only fallback used when no GROQ_API_KEY is set — the
device's typed-text path still works, but spoken audio yields no transcript.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .audio import pcm16_to_wav
from .config import Config

logger = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class STT(Protocol):
    async def transcribe(self, pcm: bytes, rate: int) -> str: ...


class NullSTT:
    async def transcribe(self, pcm: bytes, rate: int) -> str:
        logger.warning("NullSTT: no STT backend configured; dropping %d audio bytes", len(pcm))
        return ""


class GroqSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        language: str = "en",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self._client = client or httpx.AsyncClient()

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        if not pcm:
            return ""
        wav = pcm16_to_wav(pcm, rate)
        files = {"file": ("audio.wav", wav, "audio/wav")}
        data = {"model": self.model, "language": self.language, "response_format": "json"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = await self._client.post(
            GROQ_TRANSCRIBE_URL, files=files, data=data, headers=headers, timeout=30.0
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()


def make_stt(config: Config, client: httpx.AsyncClient | None = None) -> STT:
    if config.stt_backend == "groq" and config.groq_api_key:
        logger.info("STT backend: Groq (%s)", config.groq_stt_model)
        return GroqSTT(config.groq_api_key, config.groq_stt_model, config.stt_language, client)
    logger.info("STT backend: null (text-only)")
    return NullSTT()
