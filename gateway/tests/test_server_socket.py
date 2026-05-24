"""End-to-end over a real WebSocket — validates the websockets v12 handler/process_request
signatures and the binary/text framing on the wire (with fake butler/STT/TTS)."""

import json

import httpx
import websockets

from esp_gateway.config import Config
from esp_gateway.server import _connection, _process_request
from esp_gateway.session import Deps

from .fakes import FakeButler, FakeSTT, FakeTTS


async def test_ws_end_to_end_over_socket():
    deps = Deps(config=Config(allow_insecure=True), butler=FakeButler(), stt=FakeSTT(), tts=FakeTTS())

    async def handler(ws):
        await _connection(ws, deps)

    server = await websockets.serve(
        handler, "127.0.0.1", 0, process_request=_process_request
    )
    port = server.sockets[0].getsockname()[1]
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=2**20) as ws:
            await ws.send(json.dumps({
                "type": "hello", "proto": 1, "device_token": "x", "user_id": "u1",
            }))
            ready = json.loads(await ws.recv())
            assert ready["type"] == "ready" and "session_id" in ready

            await ws.send(json.dumps({"type": "text", "text": "hi"}))
            saw_card = saw_audio = False
            said = []
            while True:
                msg = await ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    saw_audio = True
                    continue
                obj = json.loads(msg)
                if obj["type"] == "say":
                    said.append(obj["text"])
                if obj["type"] == "card":
                    saw_card = True
                if obj["type"] == "state" and obj["value"] == "idle":
                    break
            assert "".join(said) == "Hello there. How are you?"
            assert saw_card and saw_audio

        # health endpoint over plain HTTP on the same port
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{port}/health")
            assert r.status_code == 200 and r.text.strip() == "ok"
    finally:
        server.close()
        await server.wait_closed()
