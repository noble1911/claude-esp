"""Pet voice matches the audition without changing normal Butler speech."""
import asyncio
import json

import httpx
import numpy as np
import pytest

from esp_gateway.audio import pcm16_to_wav
from esp_gateway.config import Config
from esp_gateway.session import Session, Deps
from esp_gateway.tts import KokoroTTS
from tests.fakes import FakeButler, FakeConn, FakeSTT, FakeTTS


@pytest.mark.asyncio
@pytest.mark.parametrize('pet', [False, True])
async def test_voice_profile_is_pet_only(pet):
    class RecordingTTS(FakeTTS):
        async def synthesize(self, *args, **kwargs):
            self.received = (args, kwargs)
            return b'\x00\x00'
    tts = RecordingTTS()
    s = Session(FakeConn(), Deps(Config(), FakeButler(), FakeSTT(), tts))
    s.pet_mode = pet
    s.voice = 'bf_emma'
    q = asyncio.Queue()
    await s._synth('Hello Sprout!', q)
    args, kwargs = tts.received
    assert args == ('Hello Sprout!', 'af_sky' if pet else 'bf_emma', s.playback_rate)
    assert kwargs == ({'speed': 1.05, 'pitch_semitones': 3.0} if pet else {})
    assert await q.get() == b'\x00\x00'


@pytest.mark.asyncio
async def test_pitch_processing_preserves_duration_and_pcm_format():
    rate = 24000
    samples = (np.sin(2 * np.pi * 440 * np.arange(rate) / rate) * 12000).astype(np.int16)
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=pcm16_to_wav(samples.tobytes(), rate))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tts = KokoroTTS('http://tts', client)
        pcm = await tts.synthesize('Hello!', 'af_sky', 16000, speed=1.05, pitch_semitones=3)
        baseline = await tts.synthesize('Hello!', 'bf_emma', 24000)
    assert baseline == samples.tobytes()
    assert seen[0]['voice'] == 'af_sky' and seen[0]['speed'] == 1.05
    assert seen[1]['voice'] == 'bf_emma' and seen[1]['speed'] == 1.0
    out = np.frombuffer(pcm, dtype='<i2')
    assert abs(len(out) / 16000 - 1) < .05
    peak = np.fft.rfftfreq(len(out), 1 / 16000)[np.argmax(abs(np.fft.rfft(out)))]
    assert abs(peak - 440 * 2 ** .25) < 3
