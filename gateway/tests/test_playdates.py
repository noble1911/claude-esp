import json
import pytest
import websockets
from esp_gateway.config import Config
from esp_gateway.playdates import Playdates, play_connection

@pytest.fixture
def game(tmp_path):
    cfg=Config(device_tokens={'a':['alice'],'b':['bob'],'c':['outsider']}, playdates_groups={'alice':'home','bob':'home','outsider':'other'},playdates_db=str(tmp_path/'play.sqlite3'))
    now=[100.]
    g=Playdates(cfg,lambda:now[0]);g.now=now
    yield g
    g.db.close()

def hello(user='alice',token='a',pet='0000000000000001'):
    return dict(type='hello',proto=1,artwork=3,user_id=user,device_token=token,pet=dict(id=pet,name='Sprout',character=0,stage=1))

def player(g,user='alice',token='a',pet='0000000000000001'):
    h=hello(user,token,pet);messages=[]
    p=g.connect(h,messages.append);p.messages=messages
    g.handle(p,dict(type='join',pet=h['pet']))
    return p

def pair(g):
    a=player(g);b=player(g,'bob','b','0000000000000002')
    g.handle(a,dict(type='invite',user='bob'))
    assert g.snapshot(b)['phase']=='incoming'
    g.handle(b,dict(type='accept',invite=b.invite))
    return a,b

def finish(g,a,b):
    for seq in range(10):
        g.now[0]+=1
        g.handle((a,b)[seq%2],dict(type='pass',room=a.room,seq=seq))
    assert g.snapshot(a)['phase']=='finished'

def test_auth_scope_and_protocol(game):
    for h in [hello(token='wrong'),hello(user='bob'),dict(hello(),artwork=2),dict(hello(),proto=2),dict(hello(),pet={})]:
        with pytest.raises(ValueError):game.connect(h,lambda x:None)
    a=player(game);player(game,'outsider','c','0000000000000003')
    assert not game.snapshot(a)['peers']
    game.handle(a,dict(type='invite',user='outsider'))
    assert not a.invite
    with pytest.raises(ValueError):player(game)

def test_presence_explicit_accept_and_crossed_invite(game):
    a=player(game);b=player(game,'bob','b','0000000000000002')
    assert game.snapshot(a)['peers'][0]['user']=='bob'
    game.handle(a,dict(type='invite',user='bob'))
    invitation=a.invite
    game.handle(b,dict(type='invite',user='alice'))
    assert a.invite==b.invite==invitation and not a.room
    game.handle(a,dict(type='accept',invite=invitation))
    assert not a.room
    game.handle(b,dict(type='accept',invite=invitation))
    assert a.room==b.room and a.room

def test_authoritative_turns_stale_duplicate_and_rewards(game):
    a,b=pair(game);room=a.room
    game.handle(b,dict(type='pass',room=room,seq=0))
    game.handle(a,dict(type='pass',room=room,seq=0))  # greeting delay
    assert game.rooms[room].seq==0
    game.now[0]+=1;game.handle(a,dict(type='pass',room=room,seq=0))
    game.now[0]+=1;game.handle(a,dict(type='pass',room=room,seq=0))
    game.handle(b,dict(type='pass',room='old',seq=1))
    assert game.rooms[room].seq==1 and game.pending(a) is None
    for seq in range(1,10):
        game.now[0]+=1;game.handle((a,b)[seq%2],dict(type='pass',room=room,seq=seq))
    assert game.pending(a)['friends']==1 and game.pending(b)['friends']==1
    game.handle(b,dict(type='pass',room=room,seq=9))
    assert game.db.execute('SELECT count(*) FROM rewards').fetchone()[0]==2
    receipt=game.pending(a)
    game.handle(b,dict(type='ack',id=receipt['id'],pet=receipt['pet']))
    assert game.pending(a)==receipt
    game.handle(a,dict(type='ack',id=receipt['id'],pet=receipt['pet']))
    assert game.pending(a) is None

def test_replay_both_opt_in_and_friend_dedup(game):
    a,b=pair(game);finish(game,a,b);old=a.room
    game.handle(a,dict(type='again',room=old));assert a.room==old
    game.handle(b,dict(type='again',room=old));assert a.room!=old and a.room==b.room
    finish(game,a,b)
    assert game.db.execute('SELECT count(*) FROM rewards').fetchone()[0]==4
    assert game.db.execute('SELECT max(friends) FROM rewards').fetchone()[0]==1

def test_reconnect_timeout_leave_and_invite_expiry(game):
    a,b=pair(game);old=a.room
    game.disconnect(b);assert game.snapshot(a)['phase']=='waiting'
    game.now[0]+=1;game.handle(a,dict(type='pass',room=old,seq=0));assert game.rooms[old].seq==0
    b=game.connect(hello('bob','b','0000000000000002'),lambda x:None)
    assert b.room==old and game.snapshot(a)['phase']=='playing'
    game.disconnect(b);game.now[0]+=46;game.tick()
    assert not a.room and not game.rooms and game.pending(a) is None
    b=player(game,'bob','b','0000000000000002')
    game.handle(a,dict(type='invite',user='bob'));game.now[0]+=31;game.tick()
    assert not a.invite and not b.invite
    game.handle(a,dict(type='invite',user='bob'));game.handle(b,dict(type='leave'))
    assert not a.invite and not game.snapshot(a)['peers']

def test_receipts_survive_server_restart_and_pet_reset(game):
    a,b=pair(game);finish(game,a,b);receipt=game.pending(a)
    restored=Playdates(game.config,game.clock)
    try:
        a2=player(restored);assert restored.pending(a2)==receipt
        restored.handle(a2,dict(type='join',pet=hello(pet='0000000000000011')['pet']))
        assert restored.pending(a2) is None  # no reward inherited by a new pet
    finally:restored.db.close()

def test_failed_reward_transaction_does_not_complete_round(game):
    a,b=pair(game)
    for seq in range(9):
        game.now[0]+=1;game.handle((a,b)[seq%2],dict(type='pass',room=a.room,seq=seq))
    game.db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON rewards WHEN NEW.user='bob' BEGIN SELECT RAISE(ABORT,'disk failed'); END")
    game.now[0]+=1
    with pytest.raises(Exception):game.handle(b,dict(type='pass',room=a.room,seq=9))
    assert game.rooms[a.room].seq==9
    assert game.pending(a) is None and game.pending(b) is None
    assert game.db.execute('SELECT count(*) FROM friends').fetchone()[0]==0

async def test_real_websocket_game_and_bad_messages(game):
    async def handler(ws):await play_connection(ws,game)
    server=await websockets.serve(handler,'127.0.0.1',0)
    uri=f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/play'
    async def send(ws,msg):await ws.send(json.dumps(msg))
    async def until(ws,predicate):
        for _ in range(30):
            msg=json.loads(await ws.recv())
            if predicate(msg):return msg
        assert False
    try:
        async with websockets.connect(uri) as a,websockets.connect(uri) as b:
            await send(a,hello());await send(a,dict(type='join',pet=hello()['pet']))
            hb=hello('bob','b','0000000000000002');await send(b,hb);await send(b,dict(type='join',pet=hb['pet']))
            await until(a,lambda m:bool(m['peers']))
            await send(a,dict(type='invite',user='bob'))
            invite=await until(b,lambda m:m['phase']=='incoming')
            await send(b,dict(type='accept',invite=invite['invite']))
            state=await until(a,lambda m:m['phase']=='playing')
            for seq in range(10):
                game.now[0]+=1
                await send((a,b)[seq%2],dict(type='pass',room=state['room'],seq=seq))
                await until(a,lambda m:m.get('seq')==seq+1)
            done=await until(b,lambda m:m['phase']=='finished')
            assert done['reward']['friends']==1
        async with websockets.connect(uri) as bad:
            await bad.send('[]')
            with pytest.raises(websockets.ConnectionClosed):await bad.recv()
    finally:
        server.close();await server.wait_closed()

async def test_timeout_publishes_offline_before_waiting_for_socket_close(game):
    import asyncio
    class TimedOutSocket:
        first=True
        async def recv(self):
            if self.first:
                self.first=False
                return json.dumps(hello())
            raise asyncio.TimeoutError()
        async def send(self,data):pass
        async def close(self,**kwargs):
            assert game.players['alice'].emit is None
    await play_connection(TimedOutSocket(),game)
