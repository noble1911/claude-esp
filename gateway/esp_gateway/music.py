"""Bounded MIDI-style pet patterns -> PCM16. No files, URLs or external synth service."""
import math
import numpy as np

def render_tune(score: dict, rate: int = 16000) -> bytes:
    # Revalidate at the playback boundary even though the pet route validates too.
    if not isinstance(score, dict) or type(rate) is not int or not 8000 <= rate <= 48000:
        raise ValueError('Invalid score or sample rate')
    tempo, instrument, notes = score.get('tempo'), score.get('instrument'), score.get('notes')
    if type(tempo) is not int or not 60 <= tempo <= 150 or instrument not in ('bell','pluck','flute'):
        raise ValueError('Invalid tempo or instrument')
    if not isinstance(notes, list) or not 4 <= len(notes) <= 24:
        raise ValueError('Expected 4..24 notes')
    total=0
    for note in notes:
        if not isinstance(note, (list,tuple)) or len(note)!=2:
            raise ValueError('Expected pitch/duration pair')
        pitch,ticks=note
        if type(pitch) is not int or type(ticks) is not int or (pitch!=0 and not 48<=pitch<=84) or not 1<=ticks<=8:
            raise ValueError('Invalid note')
        total+=ticks
    if total*15/tempo > 12 or not any(pitch for pitch,_ in notes):
        raise ValueError('Tune must sound and last at most 12 seconds')
    chunks=[]
    for pitch,ticks in notes:
        n=round(rate*ticks*15/tempo)
        if pitch==0:chunks.append(np.zeros(n,dtype='<i2'));continue
        t=np.arange(n,dtype=np.float64)/rate
        phase=2*math.pi*440*2**((pitch-69)/12)*t
        # Gentle fixed gain, short attack/release and a breathing gap per note.
        env=np.minimum(1,t/.008)*np.clip((n*.94-1-np.arange(n))/(rate*.025),0,1)
        if instrument=='bell':voice=(np.sin(phase)+.32*np.sin(2*phase)+.12*np.sin(3*phase))*np.exp(-7*t)
        elif instrument=='pluck':voice=(np.sin(phase)+.3*np.sin(2*phase))*np.exp(-13*t)
        else:voice=(np.sin(phase)+.12*np.sin(2*phase))*np.sin(math.pi*np.arange(n)/n)
        chunks.append((2600*env*voice).astype('<i2'))
    return b''.join(chunk.tobytes() for chunk in chunks)
