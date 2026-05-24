"""Per-connection session: handshake, audio buffering, and turn orchestration.

Transport-agnostic: `conn` is any object with ``async send(str | bytes)``. The server
feeds inbound messages by awaiting ``Session.handle(message)``; binary messages are mic
audio, text messages are JSON control. This keeps the session fully unit-testable with
an in-memory fake connection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from . import protocol as P
from .butler import ButlerClient
from .config import Config
from .render import SAMPLE_SVG, svg_to_png
from .stt import STT
from .tts import KokoroTTS

logger = logging.getLogger(__name__)

MAX_UTTERANCE_SECONDS = 30
_SENTENCE_END = ".!?\n"

# Gate near-silent / too-short captures before STT: Whisper hallucinates fixed
# phrases ("Thank you.", "Thanks for watching.") on silence, firing fake turns.
MIN_SPEECH_SECONDS = 0.35
MIN_SPEECH_RMS = 200.0  # int16 RMS; room silence ~<150, speech ~>1000 (tune via logs)


def audio_rms(pcm: bytes) -> float:
    """RMS amplitude of PCM16 mono bytes (0 if empty)."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


class HandshakeError(Exception):
    """Raised to signal the server to close the connection (auth failure)."""


class Connection(Protocol):
    async def send(self, data: str | bytes) -> None: ...


@dataclass
class Deps:
    config: Config
    butler: ButlerClient
    stt: STT
    tts: KokoroTTS


def pop_sentence(buf: str) -> tuple[str, str]:
    """Pop the first complete sentence from buf → (sentence, remainder).

    Returns ("", buf) when no complete sentence is present yet. Avoids splitting
    on decimals like "3.5".
    """
    for i, ch in enumerate(buf):
        if ch in _SENTENCE_END:
            end = i + 1
            if ch == "." and end < len(buf) and buf[end].isdigit():
                continue
            return buf[:end].strip(), buf[end:]
    return "", buf


class Session:
    def __init__(self, conn: Connection, deps: Deps) -> None:
        self.conn = conn
        self.deps = deps
        self.authed = False
        self.user_id: str = ""
        self.session_id: str = ""
        self.allowed_users: list[str] = []  # [] = any
        self.capture_rate = deps.config.default_capture_rate
        self.playback_rate = deps.config.default_playback_rate
        self.voice = deps.config.default_voice
        self._audio = bytearray()
        self._listening = False
        self._turn: asyncio.Task | None = None
        self._utterance_id = 0

    # ── outbound helpers ────────────────────────────────────────────
    async def _send(self, **obj) -> None:
        await self.conn.send(json.dumps(obj))

    async def _error(self, code: str, message: str = "") -> None:
        await self._send(type=P.ERROR, code=code, message=message)

    # ── inbound dispatch ────────────────────────────────────────────
    async def handle(self, message: str | bytes) -> None:
        if isinstance(message, (bytes, bytearray)):
            self._on_binary(bytes(message))
            return
        try:
            msg = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            await self._error("bad_json")
            return
        mtype = msg.get("type")
        if not self.authed and mtype != P.HELLO:
            await self._error("expected_hello")
            raise HandshakeError("first message was not hello")

        if mtype == P.HELLO:
            await self._on_hello(msg)
        elif mtype == P.AUDIO_START:
            await self._on_audio_start()
        elif mtype == P.AUDIO_END:
            await self._on_audio_end()
        elif mtype == P.TEXT:
            await self._on_text(msg)
        elif mtype == P.SET_USER:
            await self._on_set_user(msg)
        elif mtype == P.CANCEL:
            await self._cancel_turn()
            await self._send(type=P.STATE, value=P.STATE_IDLE)
        elif mtype == P.PING:
            await self._send(type=P.PONG)
        else:
            await self._error("unknown_type", str(mtype))

    async def _on_hello(self, msg: dict) -> None:
        token = msg.get("device_token", "")
        user_id = msg.get("user_id", "")
        allowed = self.deps.config.allowed_users(token)
        if allowed is None:
            await self._error("unauthorized", "invalid device token")
            raise HandshakeError("invalid device token")
        if allowed and user_id not in allowed:
            await self._error("forbidden_user", f"user {user_id!r} not allowed for this device")
            raise HandshakeError("user not allowed")
        if not user_id:
            await self._error("missing_user", "user_id required")
            raise HandshakeError("missing user_id")

        self.allowed_users = allowed
        self.user_id = user_id
        self.session_id = str(uuid.uuid4())
        self.capture_rate = int(msg.get("capture", {}).get("rate", self.capture_rate))
        self.playback_rate = int(msg.get("playback", {}).get("rate", self.playback_rate))
        voice = await self.deps.butler.get_user_voice(user_id)
        if voice:
            self.voice = voice
        self.authed = True
        logger.info(
            "session ready user=%s session=%s capture=%d playback=%d voice=%s",
            user_id, self.session_id, self.capture_rate, self.playback_rate, self.voice,
        )
        await self._send(
            type=P.READY,
            session_id=self.session_id,
            playback={"rate": self.playback_rate},
        )

    def _on_binary(self, data: bytes) -> None:
        if not self._listening:
            return
        cap = MAX_UTTERANCE_SECONDS * self.capture_rate * 2
        if len(self._audio) < cap:
            self._audio.extend(data)

    async def _on_audio_start(self) -> None:
        await self._cancel_turn()  # barge-in
        self._audio.clear()
        self._listening = True
        await self._send(type=P.STATE, value=P.STATE_LISTENING)

    async def _on_audio_end(self) -> None:
        self._listening = False
        pcm = bytes(self._audio)
        self._audio.clear()
        if pcm:
            self._start_turn(self._run_voice_turn(pcm))
        else:
            await self._send(type=P.STATE, value=P.STATE_IDLE)

    async def _on_text(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text == "/testimg":  # debug: push a sample image without the brain
            await self._send_image_svg(SAMPLE_SVG)
            return
        await self._cancel_turn()  # barge-in
        self._start_turn(self._run_turn(text))

    async def _send_image_svg(self, svg: str) -> None:
        """Rasterize an SVG to PNG and send it as a base64 JSON image message.

        Rasterization is CPU-bound, so it runs in a thread to avoid stalling the
        event loop (and the concurrent audio sender). base64 keeps the image on a
        text frame, cleanly separated from the binary TTS audio stream.
        """
        if not svg:
            return
        try:
            loop = asyncio.get_running_loop()
            png = await loop.run_in_executor(None, svg_to_png, svg)
        except Exception as e:  # noqa: BLE001 — surface render failures, don't crash the turn
            logger.exception("svg rasterize failed")
            await self._error("render_error", str(e))
            return
        b64 = base64.b64encode(png).decode("ascii")
        logger.info("→ image to device: %d B png (%d B64)", len(png), len(b64))
        await self._send(type=P.IMAGE, format="png", data=b64)

    async def _on_set_user(self, msg: dict) -> None:
        user_id = msg.get("user_id", "")
        if self.allowed_users and user_id not in self.allowed_users:
            await self._error("forbidden_user", f"user {user_id!r} not allowed")
            return
        if not user_id:
            return
        await self._cancel_turn()
        self.user_id = user_id
        voice = await self.deps.butler.get_user_voice(user_id)
        if voice:
            self.voice = voice
        logger.info("session switched user=%s", user_id)

    # ── turn lifecycle ──────────────────────────────────────────────
    def _start_turn(self, coro) -> None:
        self._turn = asyncio.create_task(coro)

    async def _cancel_turn(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
            try:
                await self._turn
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — cleanup
                pass
        self._turn = None

    async def _run_voice_turn(self, pcm: bytes) -> None:
        # Drop near-silent / too-short captures so Whisper doesn't hallucinate a
        # phrase and fire a fake turn. Logged so the RMS threshold can be tuned.
        duration = len(pcm) / (self.capture_rate * 2) if self.capture_rate else 0.0
        rms = audio_rms(pcm)
        if duration < MIN_SPEECH_SECONDS or rms < MIN_SPEECH_RMS:
            logger.info("stt gate: dropped (dur=%.2fs rms=%.0f)", duration, rms)
            await self._send(type=P.STATE, value=P.STATE_IDLE)
            return
        logger.info("stt gate: pass (dur=%.2fs rms=%.0f)", duration, rms)
        transcript = (await self.deps.stt.transcribe(pcm, self.capture_rate)).strip()
        if not transcript:
            await self._send(type=P.STATE, value=P.STATE_IDLE)
            return
        await self._send(type=P.STT, text=transcript)
        await self._run_turn(transcript)

    async def _run_turn(self, transcript: str) -> None:
        logger.info("turn user=%s transcript=%r", self.user_id, transcript[:100])
        await self._send(type=P.STATE, value=P.STATE_THINKING)
        # Producer/consumer: synthesize sentences as they arrive and enqueue the
        # PCM, while a concurrent sender streams it to the device at ~real-time.
        # Synthesis of sentence N+1 runs while sentence N is still playing, so the
        # audio never runs dry between sentences (no mid-reply gaps).
        audio_q: asyncio.Queue = asyncio.Queue()
        self._utterance_id += 1
        sender = asyncio.create_task(self._audio_sender(audio_q, self._utterance_id))
        sentence_buf = ""
        try:
            async for ev in self.deps.butler.stream_turn(
                self.user_id, self.session_id, transcript
            ):
                if ev.kind == "delta":
                    await self._send(type=P.SAY, text=ev.text)
                    sentence_buf += ev.text
                    while True:
                        sentence, sentence_buf = pop_sentence(sentence_buf)
                        if not sentence:
                            break
                        await self._synth(sentence, audio_q)
                elif ev.kind == "card":
                    logger.info("→ forwarding card to device (op=%s)", (ev.card or {}).get("op"))
                    await self._send(type=P.CARD, card=ev.card or {})
                elif ev.kind == "image":
                    await self._send_image_svg(ev.svg)
                elif ev.kind == "visual":
                    await self._send(type=P.CARD, card={"op": "text", "body": ev.text})
                elif ev.kind == "done":
                    break
            tail = sentence_buf.strip()
            if tail:
                await self._synth(tail, audio_q)
            await audio_q.put(None)  # end-of-stream sentinel
            await sender             # wait for playback to finish draining
        except asyncio.CancelledError:
            sender.cancel()
            raise  # barge-in: leave state control to the canceller
        except Exception as e:  # noqa: BLE001
            sender.cancel()
            logger.exception("turn failed for user=%s", self.user_id)
            await self._error("brain_error", str(e))
            await self._send(type=P.STATE, value=P.STATE_IDLE)
            return
        await self._send(type=P.STATE, value=P.STATE_IDLE)

    async def _synth(self, text: str, audio_q: asyncio.Queue) -> None:
        """Synthesize one sentence and enqueue its PCM (runs ahead of playback)."""
        pcm = await self.deps.tts.synthesize(text, self.voice, self.playback_rate)
        if pcm:
            await audio_q.put(pcm)

    async def _audio_sender(self, audio_q: asyncio.Queue, uid: int) -> None:
        """Drain synthesized PCM and stream it to the device, paced ~real-time."""
        frame_bytes = int(self.playback_rate * 0.02) * 2  # 20 ms PCM16 mono
        started = False
        sent = 0
        while True:
            pcm = await audio_q.get()
            if pcm is None:
                break
            if not started:
                await self._send(type=P.STATE, value=P.STATE_SPEAKING)
                await self._send(type=P.TTS_START, id=uid)
                started = True
            for i in range(0, len(pcm), frame_bytes):
                await self.conn.send(pcm[i : i + frame_bytes])
                sent += 1
                # Burst ~1 s up front so the device builds a deep jitter buffer
                # that rides out marginal-WiFi stalls, then pace ~real-time.
                if sent > 50:
                    await asyncio.sleep(0.018)
        if started:
            await self._send(type=P.TTS_END, id=uid)

    async def close(self) -> None:
        await self._cancel_turn()
