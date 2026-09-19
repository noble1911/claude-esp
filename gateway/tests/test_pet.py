import json
import asyncio
import pytest
import httpx
from esp_gateway.session import Session, Deps
from esp_gateway.config import Config
from esp_gateway.butler import ButlerClient, ButlerEvent
from tests.fakes import FakeConn, FakeButler, FakeSTT, FakeTTS

@pytest.mark.asyncio
async def test_pet_requires_fresh_state_and_locks_identity():
    conn=FakeConn(); b=FakeButler(); s=Session(conn,Deps(Config(device_tokens={"pet-token":["pet-meadow-test"]}),b,FakeSTT(),FakeTTS()))
    await s.handle(json.dumps({"type":"hello","device_token":"pet-token","user_id":"pet-meadow-test"}))
    assert s.pet_mode # Cannot escape the pet route by omitting surface.
    await s.handle(json.dumps({"type":"text","text":"hello"}))
    assert conn.json_messages()[-1]['code']=='missing_pet'
    await s.handle(json.dumps({"type":"set_user","user_id":"adult"}))
    assert s.user_id=='pet-meadow-test'
    assert conn.json_messages()[-1]['code']=='pet_user_locked'
    await s.close()

@pytest.mark.asyncio
async def test_pet_snapshot_forwarded_without_changing_transcript():
    class Brain(FakeButler):
        async def stream_turn(self,user_id,session_id,transcript,pet=None):
            self.received=(transcript,pet)
            yield ButlerEvent('done')
    b=Brain();s=Session(FakeConn(),Deps(Config(device_tokens={"t":["pet-meadow-test"]}),b,FakeSTT(),FakeTTS()))
    await s.handle(json.dumps({"type":"hello","device_token":"t","user_id":"pet-meadow-test"}))
    for fullness in (90,20):
        pet={"name":"Sprout","fullness":fullness}
        await s.handle(json.dumps({"type":"text","text":"Are you hungry?","pet":pet}))
        await s._turn
        assert b.received==('Are you hungry?',pet)
    await s.close()

@pytest.mark.asyncio
async def test_pet_client_route():
    seen=[]
    def handler(req):
        seen.append((req.url.path,json.loads(req.content)))
        return httpx.Response(200,text='data: [DONE]\n\n')
    b=ButlerClient('http://brain',client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async for _ in b.stream_turn('pet','session','hello',pet={'name':'Sprout'}):pass
    assert seen[0][0]=='/api/voice/pet/stream'
    assert seen[0][1]['pet']=={'name':'Sprout'}
    await b.aclose()

@pytest.mark.asyncio
async def test_stt_failure_recovers():
    class BrokenSTT:
        async def transcribe(self,*args):raise RuntimeError('network down')
    conn=FakeConn();s=Session(conn,Deps(Config(),FakeButler(),BrokenSTT(),FakeTTS()))
    await s._run_voice_turn(b'\xff\x1f'*16000)
    assert conn.json_messages()[-2]['code']=='stt_error'
    assert conn.json_messages()[-1]['value']=='idle'

@pytest.mark.asyncio
async def test_pet_missing_microphone_audio_is_not_silently_ignored():
    conn=FakeConn();s=Session(conn,Deps(Config(device_tokens={'t':['pet-meadow-test']}),FakeButler(),FakeSTT(),FakeTTS()))
    await s.handle(json.dumps({'type':'hello','device_token':'t','user_id':'pet-meadow-test'}))
    await s.handle(json.dumps({'type':'audio_start','pet':{'name':'Sprout'}}))
    await s.handle(json.dumps({'type':'audio_end','pet':{'name':'Sprout'}}))
    assert conn.json_messages()[-2]['code']=='no_audio'
    assert conn.json_messages()[-1]['value']=='idle'
    await s.close()

@pytest.mark.asyncio
async def test_pet_silence_gets_retry_feedback():
    conn=FakeConn();s=Session(conn,Deps(Config(),FakeButler(),FakeSTT(),FakeTTS()))
    s.pet_mode=True
    await s._run_voice_turn(b'\0'*32000)
    assert conn.json_messages()[-2]['code']=='no_speech'
    assert conn.json_messages()[-1]['value']=='idle'
