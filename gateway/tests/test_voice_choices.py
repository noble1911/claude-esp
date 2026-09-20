import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest
from esp_gateway.pet_voices import PRESETS, PREVIEW_TEXT, resolve_voice
from esp_gateway.session import Session, Deps, HandshakeError
from esp_gateway.config import Config
from esp_gateway.butler import ButlerEvent
from tests.fakes import FakeConn, FakeButler, FakeSTT
from tests.test_pet_chat import chat_game, chat_pair

class Brain(FakeButler):
    async def stream_turn(self, *args, **kwargs):
        self.received=kwargs
        yield ButlerEvent('delta', text='A tiny happy wiggle!')
        yield ButlerEvent('done')

async def session(pet=True):
    c=FakeConn();b=Brain();tts=AsyncMock()
    tts.synthesize.return_value=b'\x00\x01'*320
    s=Session(c,Deps(Config(allow_insecure=True),b,FakeSTT(),tts))
    await s.handle(json.dumps(dict(type='hello',user_id='pet-meadow-test' if pet else 'adult')))
    return s,c,b,tts

@pytest.mark.asyncio
@pytest.mark.parametrize('preset',list(PRESETS))
async def test_preview_is_fixed_text_without_brain_or_saving(preset):
    s,c,b,t=await session();s.pet_voice_id='low'
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset=preset,preview_id=12,text='Ignored arbitrary prompt')))
    await s._turn
    v=PRESETS[preset]
    t.synthesize.assert_awaited_once_with(PREVIEW_TEXT,v.voice,16000,speed=v.speed,pitch_semitones=v.pitch)
    assert not hasattr(b,'received') and s.pet_voice_id=='low' and s.pet is None
    assert c.binary_bytes() and 'say' not in c.types()
    assert all(m['preview_id']==12 for m in c.json_messages() if m['type']!='ready')
    await s.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('proactive',[False,True])
@pytest.mark.parametrize('preset',list(PRESETS))
async def test_voice_applies_to_normal_and_idle_turns(proactive,preset):
    s,c,b,t=await session()
    await s.handle(json.dumps(dict(type='text',text='Hello',pet={'name':'Olive'},voice_preset=preset,proactive=proactive)))
    await s._turn
    v=PRESETS[preset]
    t.synthesize.assert_awaited_once_with('A tiny happy wiggle!',v.voice,16000,speed=v.speed,pitch_semitones=v.pitch)
    assert b.received['pet']=={'name':'Olive'} # No synthesis settings in brain context.
    assert bool(b.received.get('proactive'))==proactive
    await s.close()

@pytest.mark.asyncio
async def test_normal_butler_ignores_pet_voice_and_rejects_preview():
    s,c,b,t=await session(False)
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset='low',preview_id=1)))
    assert c.json_messages()[-1]['code']=='pet_only';t.synthesize.assert_not_awaited()
    await s.handle(json.dumps(dict(type='text',text='Hi',voice_preset='low')));await s._turn
    t.synthesize.assert_awaited_once_with('A tiny happy wiggle!','bf_emma',16000)
    await s.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',[None,[],{},True,99,'unknown'])
async def test_unknown_preview_rejected(invalid):
    s,c,b,t=await session()
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset=invalid,preview_id=1)))
    assert c.json_messages()[-1]['code']=='invalid_voice';t.synthesize.assert_not_awaited()
    await s.close()

@pytest.mark.asyncio
async def test_preview_auth_cancellation_error_and_unchanged_saved_voice():
    s,c,b,t=await session();s.authed=False
    with pytest.raises(HandshakeError):await s.handle(json.dumps(dict(type='voice_preview',voice_preset='low',preview_id=1)))
    s.authed=True
    started=asyncio.Event()
    async def waiting(*args,**kwargs):started.set();await asyncio.Event().wait()
    t.synthesize.side_effect=waiting
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset='warm',preview_id=2)))
    pending=s._turn;await started.wait()
    await s.handle(json.dumps(dict(type='cancel')))
    assert pending.cancelled() and not c.binary_bytes() and not hasattr(b,'received')
    t.synthesize.side_effect=RuntimeError('unavailable')
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset='soft',preview_id=3)));await s._turn
    assert c.json_messages()[-1]==dict(type='error',code='preview_error',preview_id=3)
    await s.close()

@pytest.mark.asyncio
async def test_chat_speakers_keep_their_own_voices(chat_game):
    with patch('esp_gateway.playdates.asyncio.sleep',new=AsyncMock()):
        a,b,r,_=chat_pair(chat_game)
        a.pet['voice_preset']='warm';b.pet['voice_preset']='low'
        await r.chat_task
    calls=chat_game.deps.tts.synthesize.call_args_list
    assert [c.args[1] for c in calls]==['bf_emma','bm_george']*4
    assert all('voice_preset' not in p for p in chat_game.deps.butler.playdate_chat.call_args.args[0])
    for v in ('wrong',[],True):
        with pytest.raises(ValueError):chat_game.profile(dict(a.pet,voice_preset=v))

def test_older_firmware_retains_configured_pet_voice():
    cfg=Config(pet_voice='af_sky',pet_speech_speed=1.05,pet_pitch_semitones=3)
    v=resolve_voice(cfg)
    assert (v.voice,v.speed,v.pitch)==('af_sky',1.05,3)

@pytest.mark.asyncio
async def test_preview_can_be_replaced_during_playback_without_old_audio_leaking():
    s,c,b,t=await session();first=asyncio.Event()
    original_send=c.send
    async def gated(data):
        await original_send(data)
        if isinstance(data,bytes):
            first.set()
            await asyncio.Event().wait()
    c.send=gated
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset='warm',preview_id=9)))
    old=s._turn;await first.wait()
    await s.handle(json.dumps(dict(type='cancel')))
    assert old.cancelled()
    c.send=original_send;c.sent.clear()
    await s.handle(json.dumps(dict(type='voice_preview',voice_preset='low',preview_id=10)));await s._turn
    assert all(m.get('preview_id')==10 for m in c.json_messages())
    assert len(c.binary_bytes())==640
    await s.close()

@pytest.mark.asyncio
async def test_microphone_turn_uses_current_choice():
    s,c,b,t=await session()
    for kind in ('audio_start','audio_end'):
        await s.handle(json.dumps(dict(type=kind,pet={'name':'Olive'},voice_preset='low')))
        if kind=='audio_start':await s.handle(b'\xff\x1f'*16000)
    await s._turn
    assert t.synthesize.call_args.args[1]=='bm_george'
    assert b.received['pet']=={'name':'Olive'}
    await s.close()
