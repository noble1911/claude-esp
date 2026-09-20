"""Toy-only voice presets. Stable IDs/order match pet_state.c; no brain settings."""
from dataclasses import dataclass

@dataclass(frozen=True)
class PetVoice:
    voice: str
    speed: float
    pitch: float

PRESETS = {
    "tiny_sprout": PetVoice("af_heart", 1.12, 6.5),
    "sunny": PetVoice("af_bella", 1.10, 5.0),
    "soft": PetVoice("af_sky", 1.02, 4.0),
    "warm": PetVoice("bf_emma", 1.03, 0.0),
    "low": PetVoice("bm_george", 1.00, 0.0),
}
PREVIEW_TEXT = "Hello! Let's play together. I saved a little smile just for you."

def validate_preset(value):
    if not isinstance(value, str) or value not in PRESETS:
        raise ValueError("Unknown pet voice")
    return value

def resolve_voice(config, preset=None):
    # Older firmware retains the current deployment's configured creature voice.
    if preset is None:
        return PetVoice(config.pet_voice, config.pet_speech_speed, config.pet_pitch_semitones)
    return PRESETS[validate_preset(preset)]
