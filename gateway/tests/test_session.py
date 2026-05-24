import json

from esp_gateway.config import Config
from esp_gateway.session import Deps, Session

from .fakes import FakeButler, FakeConn, FakeSTT, FakeTTS


def make_session(events=None, stt_text="hello world"):
    conn = FakeConn()
    butler = FakeButler(events)
    tts = FakeTTS()
    deps = Deps(config=Config(allow_insecure=True), butler=butler, stt=FakeSTT(stt_text), tts=tts)
    return Session(conn, deps), conn, butler, tts


async def _hello(session):
    await session.handle(json.dumps({"type": "hello", "device_token": "x", "user_id": "u1"}))


async def test_handshake_ready():
    s, conn, *_ = make_session()
    await _hello(s)
    msgs = conn.json_messages()
    assert msgs[0]["type"] == "ready"
    assert s.authed and s.user_id == "u1" and s.voice == "bf_emma"


async def test_rejects_before_hello():
    s, conn, *_ = make_session()
    import pytest
    from esp_gateway.session import HandshakeError
    with pytest.raises(HandshakeError):
        await s.handle(json.dumps({"type": "text", "text": "hi"}))


async def test_text_turn_full_loop():
    s, conn, butler, tts = make_session()
    await _hello(s)
    await s.handle(json.dumps({"type": "text", "text": "hi butler"}))
    await s._turn  # let the turn task finish

    types = conn.types()
    assert butler.last_transcript == "hi butler"
    # state progression
    assert "thinking" == conn.json_messages()[1]["value"]
    assert types[-1] == "state" and conn.json_messages()[-1]["value"] == "idle"
    # two sentences synthesized, streamed as one continuous utterance
    assert tts.calls == ["Hello there.", "How are you?"]
    assert types.count("tts_start") == 1 and types.count("tts_end") == 1
    # card forwarded
    cards = [m for m in conn.json_messages() if m["type"] == "card"]
    assert cards and cards[0]["card"]["title"] == "Greeting"
    # some binary audio emitted
    assert len(conn.binary_bytes()) > 0


async def test_voice_turn_uses_stt():
    s, conn, butler, tts = make_session(stt_text="turn on the lights")
    await _hello(s)
    await s.handle(json.dumps({"type": "audio_start"}))
    await s.handle(b"\x00\x00" * 320)  # one 20ms frame of silence
    await s.handle(json.dumps({"type": "audio_end"}))
    await s._turn

    stt_events = [m for m in conn.json_messages() if m["type"] == "stt"]
    assert stt_events and stt_events[0]["text"] == "turn on the lights"
    assert butler.last_transcript == "turn on the lights"


async def test_set_user_switches():
    s, conn, butler, tts = make_session()
    await _hello(s)
    await s.handle(json.dumps({"type": "set_user", "user_id": "u2"}))
    assert s.user_id == "u2"


async def test_empty_audio_goes_idle():
    s, conn, *_ = make_session()
    await _hello(s)
    await s.handle(json.dumps({"type": "audio_start"}))
    await s.handle(json.dumps({"type": "audio_end"}))
    if s._turn:
        await s._turn
    assert conn.json_messages()[-1] == {"type": "state", "value": "idle"}
