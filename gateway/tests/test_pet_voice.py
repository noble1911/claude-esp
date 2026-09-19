"""Pet voice matches the audition without changing normal Butler speech."""
import asyncio
import json

import httpx
import numpy as np
import pytest

from esp_gateway.audio import pcm16_to_wav
from esp_gateway.config import Config, load_config
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
    assert args == ('Hello Sprout!', 'af_heart' if pet else 'bf_emma', s.playback_rate)
    assert kwargs == ({'speed': 1.12, 'pitch_semitones': 6.5} if pet else {})
    assert await q.get() == b'\x00\x00'


@pytest.mark.asyncio
@pytest.mark.parametrize("pitch", [3, 6.5, 8])
async def test_pitch_processing_preserves_duration_and_pcm_format(pitch):
    rate = 24000
    samples = (np.sin(2 * np.pi * 440 * np.arange(rate) / rate) * 12000).astype(np.int16)
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=pcm16_to_wav(samples.tobytes(), rate))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tts = KokoroTTS('http://tts', client)
        pcm = await tts.synthesize('Hello!', 'af_sky', 16000, speed=1.05, pitch_semitones=pitch)
        baseline = await tts.synthesize('Hello!', 'bf_emma', 24000)
    assert baseline == samples.tobytes()
    assert seen[0]['voice'] == 'af_sky' and seen[0]['speed'] == 1.05
    assert seen[1]['voice'] == 'bf_emma' and seen[1]['speed'] == 1.0
    out = np.frombuffer(pcm, dtype='<i2')
    assert abs(len(out) / 16000 - 1) < .05
    peak = np.fft.rfftfreq(len(out), 1 / 16000)[np.argmax(abs(np.fft.rfft(out)))]
    assert abs(peak - 440 * 2 ** (pitch/12)) < 3


@pytest.mark.asyncio
async def test_pet_short_reply_uses_one_synthesis_and_retains_captions():
    from esp_gateway.butler import ButlerEvent
    class Brain(FakeButler):
        async def stream_turn(self,*args,**kwargs):
            for text in ("Ooh!", " Little leaf", " wiggles!"):
                yield ButlerEvent("delta",text=text)
            yield ButlerEvent("done")
    conn=FakeConn();tts=FakeTTS();s=Session(conn,Deps(Config(),Brain(),FakeSTT(),tts))
    s.pet_mode=True
    await s._run_turn("Hello")
    assert tts.calls==["Ooh! Little leaf wiggles!"]
    assert ''.join(m['text'] for m in conn.json_messages() if m['type']=='say')==tts.calls[0]
    assert conn.types().count('tts_start')==1 and conn.types().count('tts_end')==1
    await s.close()


def test_pet_voice_environment_settings_are_scoped_and_bounded(monkeypatch):
    monkeypatch.setenv('PET_TTS_VOICE','am_puck')
    monkeypatch.setenv('PET_TTS_SPEED','1.10')
    monkeypatch.setenv('PET_TTS_PITCH','8')
    monkeypatch.setenv('KOKORO_VOICE','bf_emma')
    cfg=load_config()
    assert (cfg.pet_voice,cfg.pet_speech_speed,cfg.pet_pitch_semitones)==('am_puck',1.1,8)
    assert cfg.default_voice=='bf_emma'
    for key,value in [('PET_TTS_PITCH','nan'),('PET_TTS_PITCH','13'),('PET_TTS_SPEED','0')]:
        with monkeypatch.context() as patch:
            patch.setenv(key,value)
            with pytest.raises(ValueError):load_config()
