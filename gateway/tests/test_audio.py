import numpy as np

from esp_gateway.audio import pcm16_to_wav, resample_i16, to_pcm_bytes, wav_to_pcm16


def test_wav_roundtrip():
    samples = (np.sin(np.arange(16000) * 0.1) * 1000).astype(np.int16)
    wav = pcm16_to_wav(to_pcm_bytes(samples), 16000)
    out, rate = wav_to_pcm16(wav)
    assert rate == 16000
    assert len(out) == len(samples)
    assert np.array_equal(out, samples)


def test_resample_upsample_length():
    s = np.zeros(16000, dtype=np.int16)
    out = resample_i16(s, 16000, 24000)
    assert abs(len(out) - 24000) <= 1


def test_resample_downsample_length():
    s = np.zeros(24000, dtype=np.int16)
    out = resample_i16(s, 24000, 16000)
    assert abs(len(out) - 16000) <= 1


def test_resample_noop():
    s = np.arange(100, dtype=np.int16)
    assert np.array_equal(resample_i16(s, 16000, 16000), s)


def test_stereo_downmix():
    # interleaved L/R → mono mean
    stereo = np.array([0, 100, 200, 300], dtype=np.int16)  # 2 frames
    wav = pcm16_to_wav(stereo.tobytes(), 16000, channels=2)
    mono, rate = wav_to_pcm16(wav)
    assert len(mono) == 2
    assert mono[0] == 50 and mono[1] == 250
