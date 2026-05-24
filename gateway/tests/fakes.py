"""Test doubles for butler / STT / TTS and an in-memory connection."""

from __future__ import annotations

import json

import numpy as np

from esp_gateway.audio import to_pcm_bytes
from esp_gateway.butler import ButlerEvent


class FakeConn:
    """Captures everything the session sends."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, data) -> None:
        self.sent.append(data)

    def json_messages(self) -> list[dict]:
        return [json.loads(m) for m in self.sent if isinstance(m, str)]

    def binary_bytes(self) -> bytes:
        return b"".join(m for m in self.sent if isinstance(m, (bytes, bytearray)))

    def types(self) -> list[str]:
        return [m.get("type") for m in self.json_messages()]


class FakeButler:
    def __init__(self, events: list[ButlerEvent] | None = None) -> None:
        self.events = events or [
            ButlerEvent("delta", text="Hello there."),
            ButlerEvent("delta", text=" How are you?"),
            ButlerEvent("card", card={"op": "card", "title": "Greeting", "rows": ["hi"]}),
            ButlerEvent("done"),
        ]
        self.last_transcript: str | None = None

    async def get_user_voice(self, user_id: str):
        return "bf_emma"

    async def stream_turn(self, user_id: str, session_id: str, transcript: str):
        self.last_transcript = transcript
        for ev in self.events:
            yield ev


class FakeSTT:
    def __init__(self, text: str = "hello world") -> None:
        self.text = text

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        return self.text


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str, voice: str, dst_rate: int) -> bytes:
        self.calls.append(text)
        # 50 ms of silence at dst_rate
        return to_pcm_bytes(np.zeros(int(dst_rate * 0.05), dtype=np.int16))
