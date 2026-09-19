import asyncio
import json
import numpy as np
import pytest
from esp_gateway.music import render_tune
from esp_gateway.butler import ButlerEvent,parse_sse_payload
from esp_gateway.session import Session,Deps
from esp_gateway.config import Config
from tests.fakes import FakeConn,FakeButler,FakeSTT,FakeTTS
SCORE=dict(title='Tiny meadow',tempo=120,instrument='bell',notes=[[69,4],[0,2],[72,2],[76,4]])

def test_pattern_pitch_rest_duration_and_peak():
    pcm=np.frombuffer(render_tune(SCORE),dtype='<i2')
    assert len(pcm)==24000 # 12 sixteenths at 120 BPM = 1.5 sec
    assert not pcm[0] and not pcm[-1] and np.max(np.abs(pcm))<4000
    assert not np.any(pcm[8000:12000])
    peak=np.fft.rfftfreq(8000,1/16000)[np.argmax(abs(np.fft.rfft(pcm[:8000])))]
    assert abs(peak-440)<3
    assert len({render_tune({**SCORE,'instrument':i}) for i in ('bell','pluck','flute')})==3

@pytest.mark.parametrize('patch',[{'tempo':0},{'tempo':True},{'instrument':'exec'},{'notes':[[90,4]]*4},{'notes':[[60,0]]*4},{'notes':[[60,True]]*4},{'notes':[[60.5,4]]*4},{'notes':[[0,4]]*4},{'notes':[[60,8]]*24},{'notes':[[60,4]]*25}])
def test_invalid_patterns_rejected(patch):
    with pytest.raises(ValueError):render_tune({**SCORE,**patch})

def test_music_sse_is_structured_not_spoken():
    e=parse_sse_payload(json.dumps({'type':'pet_music','score':SCORE}))
    assert e.kind=='music' and e.score==SCORE and not e.text
    assert parse_sse_payload('[]') is None

class Brain(FakeButler):
    async def stream_turn(self,*args,**kwargs):
        for e in self.events:yield e

@pytest.mark.asyncio
@pytest.mark.parametrize('pet,proactive,expected',[(True,False,True),(False,False,False),(True,True,False)])
async def test_tune_follows_speech_and_is_pet_request_only(pet,proactive,expected):
    conn=FakeConn();tts=FakeTTS()
    b=Brain([ButlerEvent('delta',text='Here is your tune'),ButlerEvent('music',score=SCORE),ButlerEvent('music',score=SCORE),ButlerEvent('done')])
    s=Session(conn,Deps(Config(),b,FakeSTT(),tts));s.pet_mode=pet;s.pet={}
    await s._run_turn('Make a tune',proactive=proactive)
    assert tts.calls==['Here is your tune']
    data=conn.binary_bytes()
    assert len(data)==1600+(8000+len(render_tune(SCORE)) if expected else 0)
    if expected:assert data.endswith(render_tune(SCORE))
    assert conn.types().count('tts_start')==1 and conn.types().count('tts_end')==1
    assert conn.json_messages()[-1]=={'type':'state','value':'idle'}

@pytest.mark.asyncio
async def test_cancellation_stops_music_sender():
    conn=FakeConn();b=Brain([ButlerEvent('music',score={**SCORE,'notes':[[60,4]]*16}),ButlerEvent('done')])
    s=Session(conn,Deps(Config(),b,FakeSTT(),FakeTTS()));s.pet_mode=True;s.pet={}
    s._start_turn(s._run_turn('Make music'))
    await asyncio.sleep(.05)
    await s._cancel_turn();await asyncio.sleep(.02);n=len(conn.binary_bytes())
    await asyncio.sleep(.06);assert len(conn.binary_bytes())==n
    assert 0<n<len(render_tune({**SCORE,'notes':[[60,4]]*16}))
