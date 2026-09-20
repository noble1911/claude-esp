import sqlite3
from unittest.mock import Mock
import pytest
from esp_gateway.arcade import ArcadeRound
from esp_gateway.playdates import Playdates
from esp_gateway.config import Config

@pytest.fixture
def game():
    now=[100.]
    g=Playdates(Config(device_tokens={'a':['a'],'b':['b']},playdates_groups={'a':'h','b':'h'},playdates_db=':memory:'),clock=lambda:now[0])
    yield g,now
    g.db.close()

def pair(g,mode):
    players=[]
    for i,user in enumerate('ab'):
        pet=dict(id=f'{i+1:016x}',name='Sprout',character=i,stage=1)
        p=g.connect(dict(proto=1,artwork=3,games=1,user_id=user,device_token=user,pet=pet),lambda m:None)
        g.handle(p,dict(type='join',pet=pet));players.append(p)
    a,b=players
    g.handle(a,dict(type='invite',user='b',mode=mode));assert not g.rooms
    g.handle(b,dict(type='accept',invite=b.invite))
    return a,b,g.rooms[a.room]

def send(g,p,kind,**kw):g.handle(p,dict(type=kind,room=p.room,**kw))

@pytest.mark.parametrize('mode',['pegs','tilt'])
def test_score_challenges_two_ready_two_scores_and_joint_replay(game,mode):
    g,now=game;a,b,r=pair(g,mode)
    assert g.snapshot(a)['seed']==g.snapshot(b)['seed']!=0
    send(g,a,'score',score=100);assert not r.arcade.submitted
    send(g,a,'ready');assert r.arcade.start is None
    send(g,b,'ready');assert g.snapshot(a)['countdown_ms']==3000
    send(g,a,'score',score=100);assert not r.arcade.submitted
    now[0]+=48
    send(g,a,'score',score=100);assert g.snapshot(a)['phase']=='playing'
    send(g,a,'score',score=1000);assert r.arcade.scores==[100,0]
    send(g,b,'score',score=200);assert g.snapshot(a)['phase']=='finished'
    assert g.snapshot(a)['score']==100 and g.snapshot(b)['score']==200
    assert g.db.execute('select count(*) from rewards').fetchone()[0]==2
    send(g,b,'score',score=300);g.tick();assert g.db.execute('select count(*) from rewards').fetchone()[0]==2
    old=r.id;send(g,a,'again');assert r.id==old
    send(g,b,'again');assert r.id!=old and not r.arcade.ready and not r.arcade.done
    g.handle(a,dict(type='score',room=old,score=10000));assert not r.arcade.submitted

@pytest.mark.parametrize('score',[-1,1,999999,True,'100',None,{},1.5])
def test_invalid_scores_are_ignored(game,score):
    g,now=game;a,b,r=pair(g,'tilt');send(g,a,'ready');send(g,b,'ready');now[0]+=50
    send(g,a,'score',score=score);assert not r.arcade.submitted

def test_memory_hides_unseen_cards_rejects_bad_turns_and_keeps_matching_turn(game):
    g,now=game;a,b,r=pair(g,'memory');s=r.arcade
    assert g.snapshot(a)['cards']==[-1]*12 and g.snapshot(a)['seed']==0
    send(g,a,'ready');send(g,b,'ready');now[0]+=4
    first=0;other=next(i for i in range(1,12) if s.deck[i]!=s.deck[first])
    send(g,b,'flip',seq=0,card=0);assert not s.flips
    send(g,a,'flip',seq=0,card=first);send(g,a,'flip',seq=0,card=other);assert s.flips==[first]
    send(g,a,'flip',seq=1,card=first);assert s.flips==[first]
    send(g,a,'flip',seq=1,card=other)
    assert s.flips==[first,other]
    shown=g.snapshot(b)['cards'];assert sum(v>=0 for v in shown)==2
    send(g,a,'flip',seq=2,card=3);assert len(s.flips)==2
    now[0]+=2;g.tick();assert s.turn==1 and g.snapshot(a)['cards']==[-1]*12
    for value in range(6):
        indexes=[i for i,v in enumerate(s.deck) if v==value]
        for i in indexes:send(g,b,'flip',seq=s.seq,card=i)
        now[0]+=2;g.tick();assert s.turn==1
    assert s.done and s.scores==[0,6] and g.snapshot(a)['phase']=='finished'
    assert g.db.execute('select count(*) from rewards').fetchone()[0]==2

def test_leave_disconnect_timeout_and_older_firmware(game):
    g,now=game;a,b,r=pair(g,'pegs')
    g.disconnect(b);assert not g.rooms and g.pending(a) is None
    # Old devices still appear but cannot accept an unsupported game invite.
    b.emit=lambda m:None;g.handle(b,dict(type='join',pet=b.pet));b.games=False
    send(g,a,'join',pet=a.pet)
    g.handle(a,dict(type='invite',user='b',mode='pegs'));assert not g.invites
    assert 'update' in g.snapshot(a)['notice']
    b.games=True;g.handle(a,dict(type='invite',user='b',mode='tilt'));send(g,b,'accept',invite=b.invite)
    r=g.rooms[a.room];send(g,a,'ready');send(g,b,'ready');now[0]+=65;g.tick()
    assert r.arcade.done and 'timed out' in g.snapshot(a)['notice'] and g.pending(a) is None

def test_reward_save_failure_retries_without_publishing_false_completion(game):
    g,now=game;a,b,r=pair(g,'pegs');send(g,a,'ready');send(g,b,'ready');now[0]+=10
    reward=g.reward_round;g.reward_round=Mock(side_effect=sqlite3.OperationalError('busy'))
    send(g,a,'score',score=100);send(g,b,'score',score=200)
    assert g.snapshot(a)['phase']=='playing' and r.arcade.reward_pending
    assert g.pending(a) is None
    g.reward_round=reward;g.tick()
    assert g.snapshot(a)['phase']=='finished' and r.arcade.rewarded
    assert g.pending(a) and g.pending(b)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['pegs','memory'])
async def test_two_real_websockets_play_and_receive_same_result(game,mode):
    import asyncio,json,websockets
    from esp_gateway.playdates import play_connection
    g,now=game
    async with websockets.serve(lambda ws,path:play_connection(ws,g),'127.0.0.1',0) as server:
        port=server.sockets[0].getsockname()[1]
        async with websockets.connect(f'ws://127.0.0.1:{port}') as a,websockets.connect(f'ws://127.0.0.1:{port}') as b:
            async def send_ws(ws,**msg):await ws.send(json.dumps(msg))
            async def until(ws,predicate):
                while True:
                    msg=json.loads(await asyncio.wait_for(ws.recv(),3))
                    if predicate(msg):return msg
            for i,(ws,user) in enumerate(((a,'a'),(b,'b'))):
                pet=dict(id=f'{i+1:016x}',name='Sprout',character=i,stage=1)
                await send_ws(ws,type='hello',proto=1,artwork=3,games=1,user_id=user,device_token=user,pet=pet)
                await send_ws(ws,type='join',pet=pet)
            await until(a,lambda s:len(s['peers'])==1)
            await send_ws(a,type='invite',user='b',mode=mode)
            invite=await until(b,lambda s:s['phase']=='incoming')
            await send_ws(b,type='accept',invite=invite['invite'])
            state=await until(a,lambda s:s['phase']=='playing');room=state['room']
            await send_ws(a,type='ready',room=room);await send_ws(b,type='ready',room=room)
            await until(a,lambda s:s.get('started'))
            now[0]+=5;g.tick();await until(a,lambda s:s.get('started') and not s['countdown_ms'])
            if mode=='pegs':
                await send_ws(a,type='score',room=room,score=100)
                await send_ws(b,type='score',room=room,score=200)
            else:
                arc=g.rooms[room].arcade
                for value in range(6):
                    for i,v in enumerate(arc.deck):
                        if v==value:
                            seq=arc.seq;await send_ws(a,type='flip',room=room,seq=seq,card=i)
                            await until(a,lambda s:s.get('seq',0)>seq)
                    now[0]+=2;g.tick()
                    if value<5:await until(a,lambda s:s.get('score')==value+1)
            sa=await until(a,lambda s:s['phase']=='finished')
            sb=await until(b,lambda s:s['phase']=='finished')
            assert sa['score']==sb['peer_score'] and sb['score']==sa['peer_score']
            assert sa['reward'] and sb['reward']
            await send_ws(a,type='leave')
            await until(b,lambda s:s['phase']=='lobby')
