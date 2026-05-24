"""Client for the butler brain: POST /api/voice/stream (SSE).

Reuses the EXACT endpoint the LiveKit agent uses. Auth is the internal API key;
the user is selected by putting user_id in the request body (butler's
get_internal_or_user returns None for internal calls and reads user_id from the body).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ButlerEvent:
    kind: str  # "delta" | "card" | "visual" | "done"
    text: str = ""
    card: dict | None = None


def parse_sse_payload(payload: str) -> ButlerEvent | None:
    """Parse one SSE ``data:`` payload into a ButlerEvent (or None to skip).

    Butler emits: {"delta": "..."}  |  {"type":"visual_content","content":"..."}
    |  {"type":"device_card","card":{...}}  |  [DONE]
    """
    payload = payload.strip()
    if not payload:
        return None
    if payload == "[DONE]":
        return ButlerEvent("done")
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("non-JSON SSE payload ignored: %r", payload[:80])
        return None
    if "delta" in obj:
        return ButlerEvent("delta", text=obj.get("delta", ""))
    kind = obj.get("type")
    if kind == "device_card":
        return ButlerEvent("card", card=obj.get("card") or {})
    if kind == "visual_content":
        return ButlerEvent("visual", text=obj.get("content", ""))
    return None


class ButlerClient:
    def __init__(
        self,
        base_url: str,
        internal_api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_api_key = internal_api_key
        self._client = client or httpx.AsyncClient()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.internal_api_key:
            h["X-API-Key"] = self.internal_api_key
        return h

    async def stream_turn(
        self, user_id: str, session_id: str, transcript: str
    ) -> AsyncIterator[ButlerEvent]:
        """Stream a single conversational turn, yielding ButlerEvents."""
        body = {"transcript": transcript, "user_id": user_id, "session_id": session_id}
        timeout = httpx.Timeout(10.0, read=180.0)
        async with self._client.stream(
            "POST",
            f"{self.base_url}/api/voice/stream",
            json=body,
            headers=self._headers(),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = parse_sse_payload(line[len("data:"):])
                if ev is None:
                    continue
                yield ev
                if ev.kind == "done":
                    return

    async def get_user_voice(self, user_id: str) -> str | None:
        """Fetch the user's preferred Kokoro voice, or None on any error."""
        try:
            r = await self._client.get(
                f"{self.base_url}/api/voice/user-voice/{user_id}",
                headers=self._headers(),
                timeout=5.0,
            )
            if r.status_code == 200:
                return r.json().get("voice")
        except Exception:
            logger.warning("voice lookup failed for user=%s", user_id)
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
