"""Small, authoritative two-player rounds. Hidden memory cards never leave here."""
import random
import secrets

MODES = ('pegs', 'tilt', 'memory')

class ArcadeRound:
    def __init__(self, mode):
        self.mode=mode
        self.seed=secrets.randbelow(0xfffffffe)+1
        self.ready=set()
        self.start=None
        self.scores=[0,0]
        self.submitted=set()
        self.done=False
        self.rewarded=False
        self.reward_pending=False
        self.error=''
        self.turn=0
        self.seq=0
        self.deck=list(range(6))*2
        random.Random(self.seed).shuffle(self.deck)
        self.matched=0
        self.flips=[]
        self.hide_at=None

    def snapshot(self, index, now):
        countdown=max(0,int((self.start-now)*1000)) if self.start is not None else 0
        limit=600 if self.mode=='memory' else 180
        remaining=max(0,int((self.start+limit-now)*1000)) if self.start is not None else limit*1000
        visible=[self.deck[i] if self.matched&(1<<i) or i in self.flips else -1 for i in range(12)] if self.mode=='memory' else []
        return dict(seed=0 if self.mode=='memory' else self.seed,ready=index in self.ready,started=self.start is not None,
                    countdown_ms=countdown,remaining_ms=remaining,score=self.scores[index],peer_score=self.scores[1-index],
                    submitted=index in self.submitted,peer_submitted=1-index in self.submitted,
                    my_turn=self.turn==index and self.start is not None and now>=self.start and self.hide_at is None and not self.done,
                    seq=self.seq,matched=self.matched,cards=visible,notice=self.error)

    def action(self,index,msg,now):
        kind=msg.get('type')
        if self.done:return
        if kind=='ready':
            self.ready.add(index)
            if len(self.ready)==2 and self.start is None:self.start=now+3
            return
        if self.start is None or now<self.start:return
        if kind=='score' and self.mode in ('pegs','tilt') and index not in self.submitted:
            score=msg.get('score')
            if type(score) is not int or score<0:return
            if self.mode=='pegs' and (score>200000 or score%5 or now<self.start+1):return
            if self.mode=='tilt' and (score>50000 or score%100 or now<self.start+44):return
            self.scores[index]=score;self.submitted.add(index);self.seq+=1
            self.done=len(self.submitted)==2
        if kind=='flip' and self.mode=='memory' and self.turn==index and self.hide_at is None:
            card=msg.get('card')
            if type(card) is not int or not 0<=card<12 or type(msg.get('seq')) is not int or msg['seq']!=self.seq:return
            if self.matched&(1<<card) or card in self.flips:return
            self.flips.append(card);self.seq+=1
            if len(self.flips)==2:self.hide_at=now+1.2

    def tick(self,now):
        changed=False
        if self.done or self.reward_pending:return False
        if self.hide_at is not None and now>=self.hide_at:
            a,b=self.flips
            if self.deck[a]==self.deck[b]:
                self.matched|=(1<<a)|(1<<b);self.scores[self.turn]+=1
            else:self.turn=1-self.turn
            self.flips=[];self.hide_at=None;self.seq+=1;changed=True
            if self.matched==4095:self.done=True;self.submitted={0,1}
        if self.start is not None and now>=self.start+(660 if self.mode=='memory' else 210) and not self.done:
            self.done=True;self.error='Round timed out. Try playing again.';changed=True
        return changed
