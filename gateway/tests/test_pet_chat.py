import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from esp_gateway.config import Config
from esp_gateway.playdates import Playdates
def hello(user='alice',token='a',pet='0000000000000001'):
    return dict(type='hello',proto=1,artwork=3,user_id=user,device_token=token,
                pet=dict(id=pet,name='Sprout',character=0,stage=1))

@pytest.fixture
def chat_game(tmp_path):
    config=Config(device_tokens={'a':['alice'],'b':['bob']},playdates_groups={'alice':'home','bob':'home'},playdates_db=str(tmp_path/'chat.sqlite3'))
    deps=SimpleNamespace(butler=SimpleNamespace(playdate_chat=AsyncMock(return_value=['Hello, shall we dream of a tiny picnic?']*8)),tts=SimpleNamespace(synthesize=AsyncMock(return_value=b'\x00\x01'*320)))
    game=Playdates(config,deps=deps)
    yield game
    game.db.close()

def chat_pair(game,speak=None):
    heard=[]
    def add(user,token,pet):
        h=hello(user,token,pet);h['chat']=1
        async def speech(room,seq,pcm):
            heard.append((user,seq,pcm))
            if speak:await speak(p,room,seq,pcm)
            else:game.handle(p,dict(type='heard',room=room,seq=seq))
        p=game.connect(h,lambda msg:None,speech)
        game.handle(p,dict(type='join',pet=h['pet']))
        return p
    a=add('alice','a','0000000000000001');b=add('bob','b','0000000000000002')
    game.handle(a,dict(type='invite',user='bob',mode='chat'))
    assert game.snapshot(b)['mode']=='chat' and not game.rooms
    game.handle(b,dict(type='accept',invite=b.invite))
    return a,b,game.rooms[a.room],heard

@pytest.mark.asyncio
async def test_eight_alternating_turns_one_call_no_rewards_and_joint_replay(chat_game):
    g=chat_game
    with patch('esp_gateway.playdates.asyncio.sleep',new=AsyncMock()):
        a,b,r,heard=chat_pair(g)
        await r.chat_task
        assert [s[:2] for s in heard]==[('alice' if i%2==0 else 'bob',i+1) for i in range(8)]
        assert g.snapshot(a)['phase']=='finished' and r.chat_done
        assert g.deps.butler.playdate_chat.await_count==1 and g.deps.tts.synthesize.await_count==8
        assert g.db.execute('SELECT count(*) FROM rewards').fetchone()[0]==0
        assert g.db.execute('SELECT count(*) FROM friends').fetchone()[0]==0
        g.handle(a,dict(type='again',room=r.id));assert g.deps.butler.playdate_chat.await_count==1
        g.handle(b,dict(type='again',room=r.id));await r.chat_task
        assert g.deps.butler.playdate_chat.await_count==2
        sent=g.deps.butler.playdate_chat.call_args.args[0]
        assert set(sent[0])=={'user_id','name','character','stage','fullness','happiness','energy','cleanliness','stars','personality'}

@pytest.mark.asyncio
async def test_disconnect_cancels_pending_generation_without_retry(chat_game):
    g=chat_game;started=asyncio.Event();gate=asyncio.Event()
    async def pending(pets):started.set();await gate.wait()
    g.deps.butler.playdate_chat.side_effect=pending
    a,b,r,_=chat_pair(g);await started.wait();g.disconnect(b)
    await asyncio.gather(r.chat_task,return_exceptions=True)
    assert r.chat_task.cancelled() and not g.rooms
    assert g.snapshot(a)['phase']=='lobby'
    assert g.deps.butler.playdate_chat.await_count==1
    g.deps.tts.synthesize.assert_not_awaited()

@pytest.mark.asyncio
async def test_wrong_ack_and_pass_cannot_advance_chat(chat_game):
    g=chat_game;started=asyncio.Event();gate=asyncio.Event()
    async def hold(p,room,seq,pcm):started.set();await gate.wait()
    a,b,r,_=chat_pair(g,hold)
    await started.wait()
    g.handle(b,dict(type='heard',room=r.id,seq=1))
    g.handle(a,dict(type='heard',room='stale',seq=1))
    g.handle(a,dict(type='heard',room=r.id,seq=0))
    g.handle(a,dict(type='pass',room=r.id,seq=1))
    assert not r.heard.is_set() and r.seq==1
    g.handle(a,dict(type='heard',room=r.id,seq=1));assert r.heard.is_set()
    g.leave(a);await asyncio.gather(r.chat_task,return_exceptions=True)
    assert r.chat_task.cancelled() and not g.rooms

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['brain','tts','empty_audio'])
async def test_failures_finish_once_with_visible_retry_and_no_reward(chat_game,failure):
    g=chat_game
    if failure=='brain':g.deps.butler.playdate_chat.side_effect=TimeoutError()
    elif failure=='tts':g.deps.tts.synthesize.side_effect=TimeoutError()
    else:g.deps.tts.synthesize.return_value=b''
    a,b,r,_=chat_pair(g);await r.chat_task
    state=g.snapshot(a)
    assert state['phase']=='finished' and 'hiccup' in state['notice'] and not state['speaking']
    assert g.deps.butler.playdate_chat.await_count==1
    assert g.pending(a) is None

def test_older_device_cannot_be_invited_to_chat(chat_game):
    g=chat_game
    a=g.connect(hello(),lambda msg:None)
    g.handle(a,dict(type='join',pet=hello()['pet']))
    h=hello('bob','b','0000000000000002');b=g.connect(h,lambda msg:None)
    g.handle(b,dict(type='join',pet=h['pet']))
    g.handle(a,dict(type='invite',user='bob',mode='chat'))
    assert not g.invites and 'update' in g.snapshot(a)['notice']
    g.deps.butler.playdate_chat.assert_not_called()
    g.handle(a,dict(type='invite',user='bob',mode='ball'));assert g.invites

@pytest.mark.asyncio
async def test_two_websockets_deliver_audio_only_to_speaker_and_stop(chat_game):
    import json
    import websockets
    from esp_gateway.playdates import play_connection
    g=chat_game
    async with websockets.serve(lambda ws,path:play_connection(ws,g),'127.0.0.1',0) as server:
        port=server.sockets[0].getsockname()[1]
        async with websockets.connect(f'ws://127.0.0.1:{port}') as a,websockets.connect(f'ws://127.0.0.1:{port}') as b:
            for ws,h in [(a,hello()),(b,hello('bob','b','0000000000000002'))]:
                h['chat']=1;await ws.send(json.dumps(h));await ws.send(json.dumps(dict(type='join',pet=h['pet'])))
            async def until(ws,predicate):
                while True:
                    raw=await asyncio.wait_for(ws.recv(),5)
                    assert isinstance(raw,str)
                    msg=json.loads(raw)
                    if predicate(msg):return msg
            await until(a,lambda m:len(m.get('peers',[]))==1)
            await a.send(json.dumps(dict(type='invite',user='bob',mode='chat')))
            invitation=await until(b,lambda m:m.get('phase')=='incoming')
            await b.send(json.dumps(dict(type='accept',invite=invitation['invite'])))
            first=await until(a,lambda m:m.get('type')=='chat_audio_start')
            pcm=await asyncio.wait_for(a.recv(),5);assert isinstance(pcm,bytes) and pcm
            end=await until(a,lambda m:m.get('type')=='chat_audio_end')
            assert end['seq']==first['seq']==1
            await a.send(json.dumps(dict(type='heard',room=end['room'],seq=1)))
            # b receives captions, then only its own turn's audio.
            second=await until(b,lambda m:m.get('type')=='chat_audio_start')
            assert second['seq']==2
            pcm=await asyncio.wait_for(b.recv(),5);assert isinstance(pcm,bytes)
            await until(b,lambda m:m.get('type')=='chat_audio_end')
            await b.send(json.dumps(dict(type='leave')))
            left=await until(a,lambda m:m.get('phase')=='lobby')
            assert 'home' in left['notice'] and not g.rooms
            assert g.deps.butler.playdate_chat.await_count==1
