"""WebSocket server: accepts device connections and runs a Session per connection."""

from __future__ import annotations

import asyncio
import http
import logging

import httpx
import websockets

from .butler import ButlerClient
from .config import Config, load_config
from .session import Deps, HandshakeError, Session
from .stt import make_stt
from .tts import KokoroTTS

logger = logging.getLogger(__name__)


def build_deps(
    config: Config, http_client: httpx.AsyncClient | None = None
) -> tuple[Deps, httpx.AsyncClient]:
    client = http_client or httpx.AsyncClient()
    butler = ButlerClient(config.butler_url, config.internal_api_key, client)
    stt = make_stt(config, client)
    tts = KokoroTTS(config.kokoro_url, client)
    return Deps(config=config, butler=butler, stt=stt, tts=tts), client


async def _connection(websocket, deps: Deps) -> None:
    session = Session(websocket, deps)
    peer = getattr(websocket, "remote_address", "?")
    logger.info("device connected: %s", peer)
    try:
        async for message in websocket:
            await session.handle(message)
    except HandshakeError as e:
        await websocket.close(code=4001, reason=str(e)[:120])
    except websockets.ConnectionClosed:
        pass
    finally:
        await session.close()
        logger.info("device disconnected: %s", peer)


async def _process_request(path, request_headers):
    """Serve a plain HTTP 200 on /health (for Docker/Cloudflare healthchecks)."""
    if path == "/health":
        return http.HTTPStatus.OK, [("Content-Type", "text/plain")], b"ok\n"
    return None


async def serve(config: Config | None = None) -> None:
    config = config or load_config()
    deps, client = build_deps(config)

    async def handler(websocket):
        await _connection(websocket, deps)

    async with websockets.serve(
        handler,
        config.host,
        config.port,
        process_request=_process_request,
        ping_interval=20,
        ping_timeout=60,  # tolerate brief device-side latency without killing the session
        max_size=2**20,
    ):
        logger.info("esp-gateway listening on ws://%s:%d", config.host, config.port)
        try:
            await asyncio.Future()  # run forever
        finally:
            await client.aclose()
