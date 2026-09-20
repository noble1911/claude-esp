"""Household-only ball games and bounded, opt-in pet conversations.

Coordinator mutations are synchronous on the asyncio owner. SQLite commits both
players' reward receipts before a finished round is published. Socket writers
are independent, so a slow peer cannot block a turn or another device.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)
HEX = re.compile(r'^[0-9a-f]{16}$')
NAME = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,14}$")

@dataclass
class Player:
    user: str
    group: str
    pet: dict
    emit: object
    active: bool = False
    seen: float = 0
    room: str = ''
    invite: str = ''
    notice: str = ''
    chat: bool = False
    speak: object = None

@dataclass
class Room:
    id: str
    users: tuple
    pets: tuple
    seq: int = 0
    next_at: float = 0
    again: set = field(default_factory=set)
    mode: str = 'ball'
    chat_task: object = None
    heard: object = None
    line: str = ''
    speaking: bool = False
    chat_done: bool = False
    error: str = ''

class Playdates:
    def __init__(self, config, clock=time.monotonic, deps=None):
        self.config = config
        self.deps = deps
        self.clock = clock
        self.players = {}
        self.rooms = {}
        self.invites = {}  # id -> (sender, recipient, expiry)
        path = config.playdates_db
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, round TEXT NOT NULL,
            user TEXT NOT NULL, pet TEXT NOT NULL, friend TEXT NOT NULL,
            friends INTEGER NOT NULL, ack INTEGER NOT NULL DEFAULT 0,
            UNIQUE(round,user,pet));
          CREATE TABLE IF NOT EXISTS friends (
            user TEXT NOT NULL, pet TEXT NOT NULL, friend TEXT NOT NULL,
            PRIMARY KEY(user,pet,friend));
        ''')

    @staticmethod
    def profile(value):
        if not isinstance(value, dict) or not HEX.fullmatch(str(value.get('id', ''))):
            raise ValueError('Invalid pet identity')
        if not NAME.fullmatch(str(value.get('name', ''))):
            raise ValueError('Invalid pet name')
        if type(value.get('character')) is not int or not 0 <= value['character'] < 8:
            raise ValueError('Update your pet to play together')
        if type(value.get('stage')) is not int or not 1 <= value['stage'] <= 5:
            raise ValueError('Invalid pet stage')
        result = {k: value[k] for k in ('id', 'name', 'character', 'stage')}
        for key, maximum, default in [('fullness',100,80),('happiness',100,80),('energy',100,80),
                                      ('cleanliness',100,80),('stars',4294967295,0),('personality',7,0)]:
            v = value.get(key,default)
            if type(v) is not int or not 0 <= v <= maximum:
                raise ValueError('Invalid pet needs')
            result[key]=v
        return result

    def connect(self, msg, emit, speak=None):
        user, token = msg.get('user_id'), msg.get('device_token')
        if not isinstance(user, str) or not isinstance(token, str):
            raise ValueError('Device registration required')
        allowed = self.config.allowed_users(token)
        group = self.config.playdates_groups.get(user)
        # Explicit household membership AND an explicitly scoped device token.
        if not group or not allowed or user not in allowed:
            raise ValueError('Device registration required')
        if type(msg.get('proto')) is not int or msg.get('proto') != 1 or msg.get('artwork') != 3:
            raise ValueError('Update your pet to play together')
        pet = self.profile(msg.get('pet'))
        old = self.players.get(user)
        if old and old.emit is not None:
            raise ValueError('This device is already connected')
        if old and old.pet['id'] == pet['id']:
            old.emit = emit
            old.seen = self.clock()
            old.pet = pet
            player = old
        else:
            if old:
                self.leave(old)
            player = Player(user, group, pet, emit, seen=self.clock())
            self.players[user] = player
        player.chat = msg.get('chat') == 1 and speak is not None and self.deps is not None
        player.speak = speak
        LOG.info('playdates connected user=%s', user)
        self.broadcast()
        return player

    def disconnect(self, p):
        if self.players.get(p.user) is not p:
            return
        p.emit = None
        p.seen = self.clock()
        if p.room in self.rooms and self.rooms[p.room].mode == 'chat':
            self.leave(p)
        # Lobby invitations end immediately. An accepted room has a reconnect grace.
        if p.invite:
            self.cancel_invite(p.invite, 'Your friend left. Try again later.')
        self.broadcast()

    def available(self, p):
        return p.active and p.emit is not None and not p.room and not p.invite

    def peer(self, p):
        return {**{k:p.pet[k] for k in ('id','name','character','stage')}, 'user':p.user,'chat':p.chat}

    def pending(self, p):
        row = self.db.execute('SELECT id,friend,friends FROM rewards WHERE user=? AND pet=? AND ack=0 ORDER BY id LIMIT 1', (p.user,p.pet['id'])).fetchone()
        return dict(id=str(row[0]), pet=p.pet['id'], friend=row[1], friends=row[2]) if row else None

    def snapshot(self, p):
        msg = dict(type='play_state', phase='lobby' if p.active else 'closed', peers=[], notice=p.notice, reward=self.pending(p))
        if p.room in self.rooms:
            room = self.rooms[p.room]
            peer = self.players[room.users[1] if room.users[0] == p.user else room.users[0]]
            online = peer.emit is not None and peer.active
            done = room.chat_done if room.mode == 'chat' else room.seq == 10
            msg.update(phase='finished' if done else 'playing' if online else 'waiting',
                       room=room.id, seq=room.seq, peer=self.peer(peer), online=online,
                       my_turn=room.users[room.seq % 2] == p.user and online and room.seq < 10,
                       again=p.user in room.again, mode=room.mode)
            if room.mode == 'chat':
                msg.update(text=room.line, speaking=room.speaking, notice=room.error,
                           my_turn=room.users[max(0,room.seq-1)%2] == p.user)
        elif p.invite in self.invites:
            sender, recipient, _, mode = self.invites[p.invite]
            other = recipient if sender == p.user else sender
            msg.update(phase='outgoing' if sender == p.user else 'incoming', invite=p.invite, peer=self.peer(self.players[other]), mode=mode)
        elif p.active:
            msg['peers'] = [self.peer(other) for other in self.players.values()
                            if other.user != p.user and other.group == p.group and self.available(other)][:4]
        return msg

    def broadcast(self):
        for p in list(self.players.values()):
            if p.emit:
                p.emit(self.snapshot(p))

    def cancel_invite(self, invite, notice=''):
        entry = self.invites.pop(invite, None)
        if entry:
            for user in entry[:2]:
                p = self.players[user]
                p.invite = ''
                p.notice = notice

    def leave(self, p):
        if p.invite:
            self.cancel_invite(p.invite)
        if p.room:
            room = self.rooms.pop(p.room, None)
            if room:
                if room.chat_task and not room.chat_task.done() and room.chat_task is not asyncio.current_task():
                    room.chat_task.cancel()
                for user in room.users:
                    self.players[user].room = ''
                    self.players[user].notice = 'Your friend went home. See you soon!'
        p.room = ''
        p.active = False

    def reward_round(self, room):
        # No completion state is visible unless both durable receipts exist.
        with self.db:
            for i, user in enumerate(room.users):
                pet, friend = room.pets[i], room.pets[1-i]
                self.db.execute('INSERT OR IGNORE INTO friends VALUES (?,?,?)', (user, pet, friend))
                count = self.db.execute('SELECT count(*) FROM friends WHERE user=? AND pet=?', (user,pet)).fetchone()[0]
                self.db.execute('INSERT OR IGNORE INTO rewards(round,user,pet,friend,friends) VALUES (?,?,?,?,?)', (room.id,user,pet,friend,min(count,65535)))

    def handle(self, p, msg):
        if self.players.get(p.user) is not p or p.emit is None:
            return
        kind = msg.get('type')
        p.seen = self.clock()
        if kind == 'ping':
            p.emit(self.snapshot(p))
            return
        if kind == 'join':
            pet = self.profile(msg.get('pet'))
            if pet['id'] != p.pet['id'] or pet != p.pet:
                self.leave(p)
                p.pet = pet
            p.active = True
            p.notice = ''
        elif kind == 'leave':
            self.leave(p)
        elif kind == 'invite' and self.available(p):
            other = self.players.get(msg.get('user'))
            mode = msg.get('mode','ball')
            if mode not in ('ball','chat') or (mode == 'chat' and not (p.chat and other and other.chat)):
                p.notice='Both pets need the Pet chat update.'
                self.broadcast()
                return
            if other and other.user != p.user and other.group == p.group and self.available(other) and other.pet['id'] != p.pet['id']:
                invite = uuid.uuid4().hex
                self.invites[invite] = (p.user, other.user, self.clock()+30, mode)
                p.invite = other.invite = invite
                p.notice = other.notice = ''
            else:
                p.notice = 'Your friend is busy. Try again soon.'
        elif kind in ('accept', 'decline') and p.invite and msg.get('invite') == p.invite:
            sender, recipient, expires, mode = self.invites[p.invite]
            if kind == 'decline':
                self.cancel_invite(p.invite, 'Maybe later. Choose a friend to play.')
            elif p.user == recipient and expires > self.clock() and self.players[sender].emit:
                self.cancel_invite(p.invite)
                room = Room(uuid.uuid4().hex, (sender,recipient), (self.players[sender].pet['id'],p.pet['id']), next_at=self.clock()+.8, mode=mode)
                self.rooms[room.id] = room
                for user in room.users:
                    self.players[user].room = room.id
                if mode == 'chat':
                    room.chat_task=asyncio.create_task(self.chat_round(room))
        elif kind == 'heard' and p.room in self.rooms and msg.get('room') == p.room:
            room=self.rooms[p.room]
            if room.mode == 'chat' and room.speaking and type(msg.get('seq')) is int and msg['seq']==room.seq and room.users[(room.seq-1)%2]==p.user:
                room.heard.set()
        elif kind in ('pass', 'again') and p.room in self.rooms and msg.get('room') == p.room:
            room = self.rooms[p.room]
            online = all(self.players[u].emit and self.players[u].active for u in room.users)
            if kind == 'pass' and room.mode == 'ball' and online and room.seq < 10 and type(msg.get('seq')) is int and msg['seq'] == room.seq and room.users[room.seq % 2] == p.user and self.clock() >= room.next_at:
                if room.seq == 9:
                    self.reward_round(room)
                room.seq += 1
                room.next_at = self.clock()+.8
            elif kind == 'again' and online and (room.chat_done if room.mode=='chat' else room.seq == 10):
                room.again.add(p.user)
                if len(room.again) == 2:
                    del self.rooms[room.id]
                    room.id = uuid.uuid4().hex
                    room.seq = 0
                    room.line='';room.chat_done=False;room.error=''
                    room.again.clear()
                    room.next_at = self.clock()+.8
                    self.rooms[room.id] = room
                    for user in room.users:
                        self.players[user].room = room.id
                    if room.mode=='chat':
                        room.chat_task=asyncio.create_task(self.chat_round(room))
        elif kind == 'ack':
            receipt = self.pending(p)
            if receipt and msg.get('id') == receipt['id'] and msg.get('pet') == p.pet['id']:
                with self.db:
                    self.db.execute('UPDATE rewards SET ack=1 WHERE id=? AND user=? AND pet=?', (receipt['id'],p.user,p.pet['id']))
        self.broadcast()

    def tick(self):
        now = self.clock()
        changed = False
        for key, (_,_,expires,_) in list(self.invites.items()):
            if now >= expires:
                self.cancel_invite(key, 'Invitation finished. Try again!')
                changed = True
        for p in list(self.players.values()):
            if p.emit is None and now-p.seen >= 45:
                self.leave(p)
                del self.players[p.user]
                changed = True
        if changed:
            self.broadcast()

    async def chat_round(self, room):
        """One Haiku call, eight alternating lines, no rewards or memory writes."""
        try:
            pets=[dict(user_id=u, **{k:v for k,v in self.players[u].pet.items() if k!='id'}) for u in room.users]
            lines=await asyncio.wait_for(self.deps.butler.playdate_chat(pets),45)
            for index,line in enumerate(lines):
                speaker=self.players[room.users[index%2]]
                pcm=await asyncio.wait_for(self.deps.tts.synthesize(line,self.config.pet_voice,16000,
                    speed=self.config.pet_speech_speed,pitch_semitones=self.config.pet_pitch_semitones),25)
                if not pcm or len(pcm)>480000 or len(pcm)%2:
                    raise ValueError('Invalid chat audio')
                room.seq=index+1;room.line=line;room.speaking=True;room.heard=asyncio.Event()
                self.broadcast()
                start=self.clock()
                await speaker.speak(room.id,room.seq,pcm)
                await asyncio.wait_for(room.heard.wait(),12)
                # Keep each caption readable even when playback is muted.
                await asyncio.sleep(max(.5,3-(self.clock()-start)))
                room.speaking=False
                self.broadcast()
            room.chat_done=True
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.warning('Pet chat failed',exc_info=True)
            room.error='A little hiccup. Try Chat again.'
            room.chat_done=True
        finally:
            room.speaking=False
            if self.rooms.get(room.id) is room:
                self.broadcast()

async def play_connection(ws, game):
    queue = asyncio.Queue(maxsize=16)
    send_lock = asyncio.Lock()
    player = None
    writer = None
    writing = True
    def emit(msg):
        # Every message is a full snapshot. Coalesce when a peer is slow.
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(json.dumps(msg))
    async def speak(room, seq, pcm):
        # One socket writer at a time; speech is paced to the device's 96 KiB buffer.
        async with send_lock:
            await ws.send(json.dumps(game.snapshot(player)))
            await ws.send(json.dumps(dict(type='chat_audio_start',room=room,seq=seq)))
            for offset in range(0,len(pcm),640):
                await asyncio.wait_for(ws.send(pcm[offset:offset+640]),5)
                if offset>=32000:
                    await asyncio.sleep(.020)
            await ws.send(json.dumps(dict(type='chat_audio_end',room=room,seq=seq)))
    async def write():
        while writing:
            try:
                await queue.get()
                async with send_lock:
                    await asyncio.wait_for(ws.send(json.dumps(game.snapshot(player))), timeout=5)
            except Exception:
                await ws.close(code=4000, reason='Reconnect to play')
                return
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        if not isinstance(raw,str) or len(raw)>4096:
            raise ValueError('Invalid hello')
        msg = json.loads(raw)
        if not isinstance(msg,dict) or msg.get('type') != 'hello':
            raise ValueError('Expected hello')
        player = game.connect(msg, emit, speak)
        writer = asyncio.create_task(write())
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=18)
            if not isinstance(raw,str) or len(raw)>4096:
                raise ValueError('Invalid game message')
            msg = json.loads(raw)
            if not isinstance(msg,dict):
                raise ValueError('Invalid game message')
            game.handle(player,msg)
    except (ValueError, TypeError, KeyError) as exc:
        if player:
            game.disconnect(player)
            player = None
        await ws.close(code=4001, reason=str(exc)[:100])
    except Exception:
        LOG.info('playdates connection ended', exc_info=False)
        if player:
            game.disconnect(player)
            player = None
        await ws.close(code=4000, reason='Reconnect to play')
    finally:
        writing = False
        if writer:
            writer.cancel()
            await asyncio.gather(writer,return_exceptions=True)
        if player:
            game.disconnect(player)
