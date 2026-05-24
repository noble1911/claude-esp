"""Hardware-free stand-in for the ESP32 — drive the gateway from the command line.

Examples:
    python -m esp_gateway.devclient --text "what's the weather in London?"
    python -m esp_gateway.devclient --wav sample.wav --out reply.wav

Sends a hello handshake, then either a typed text turn (skips STT) or streams a WAV
file as mic audio (exercises STT). Prints every event and saves returned TTS to --out.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import wave

import websockets

from .audio import resample_i16, to_pcm_bytes, wav_to_pcm16


async def run(url: str, token: str, user: str, text: str, wav_path: str, out: str) -> None:
    async with websockets.connect(url, max_size=2**20) as ws:
        await ws.send(json.dumps({
            "type": "hello", "proto": 1, "device_token": token, "user_id": user,
            "capture": {"rate": 16000}, "playback": {"rate": 16000},
        }))
        print("<<", await ws.recv())

        if text:
            await ws.send(json.dumps({"type": "text", "text": text}))
        elif wav_path:
            with wave.open(wav_path, "rb") as w:
                raw = w.readframes(w.getnframes())
                rate = w.getframerate()
            import numpy as np
            samples = np.frombuffer(raw, dtype="<i2")
            pcm = to_pcm_bytes(resample_i16(samples, rate, 16000))
            await ws.send(json.dumps({"type": "audio_start"}))
            for i in range(0, len(pcm), 640):  # 20 ms frames
                await ws.send(pcm[i : i + 640])
                await asyncio.sleep(0.005)
            await ws.send(json.dumps({"type": "audio_end"}))

        audio = bytearray()
        said: list[str] = []
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=45)
            except asyncio.TimeoutError:
                print("(timeout)")
                break
            if isinstance(msg, (bytes, bytearray)):
                audio.extend(msg)
                continue
            obj = json.loads(msg)
            if obj.get("type") == "image":
                png = base64.b64decode(obj.get("data", ""))
                path = obj.get("format", "png")
                fn = f"device_image.{path}"
                with open(fn, "wb") as f:
                    f.write(png)
                print(f"<< image: {len(png)} bytes -> {fn}")
                if text.startswith("/"):  # debug command (e.g. /testimg): no turn follows
                    break
                continue
            print("<<", obj)
            if obj.get("type") == "say" and isinstance(obj.get("text"), str):
                said.append(obj["text"])
            if obj.get("type") == "state" and obj.get("value") == "idle":
                break

        print("\nASSISTANT:", "".join(said))
        if audio and out:
            with wave.open(out, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(audio))
            print(f"wrote {out} ({len(audio)} bytes PCM @16k)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8770")
    ap.add_argument("--token", default=os.environ.get("GATEWAY_DEVICE_TOKEN", "dev"))
    ap.add_argument("--user", default=os.environ.get("DEV_USER_ID", "invite_butler_001"))
    ap.add_argument("--text", default="")
    ap.add_argument("--wav", default="")
    ap.add_argument("--out", default="reply.wav")
    args = ap.parse_args()
    asyncio.run(run(args.url, args.token, args.user, args.text, args.wav, args.out))


if __name__ == "__main__":
    main()
