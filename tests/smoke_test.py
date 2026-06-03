"""
LIVE smoke test for the optional Aporto wrapper.

This is NOT part of the regular test suite. It costs ~$0.001 per run.

Run manually with a real key:
    APORTO_API_KEY=***hon3 tests/smoke_test.py

What it checks end-to-end:
  1. aporto_client imports cleanly without GOOGLE_API_KEY
  2. transcribe_audio() round-trips through Aporto (skill 294)
"""
import asyncio
import base64
import math
import os
import struct
import sys
import tempfile
import wave


def make_tiny_wav() -> bytes:
    """1-second mono 16kHz 440Hz tone WAV — Gemini hallucinates on it,
    but the test only proves that the wrapper reaches Aporto and parses
    the response shape, not that the transcript is correct."""
    sample_rate = 16000
    n = sample_rate
    pcm = b"".join(
        struct.pack("<h", int(32767 * 0.3 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        for i in range(n)
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        path = f.name
    data = open(path, "rb").read()
    os.unlink(path)
    return data


class FakeStatusMsg:
    def __init__(self):
        self.last_text = None
    async def edit_text(self, text):
        self.last_text = text


async def main():
    api_key = os.environ.get("APORTO_API_KEY") or os.environ.get("API_APORTO_TECH_API_KEY")
    if not api_key:
        print("ERROR: APORTO_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    os.environ["APORTO_API_KEY"] = api_key

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from aporto_client import transcribe_audio, INTEGRATION_ID, APORTO_SKILLS

    print(f"INTEGRATION_ID: {INTEGRATION_ID}")
    print(f"APORTO_SKILLS: {APORTO_SKILLS}")

    audio = make_tiny_wav()
    print(f"Generated test audio: {len(audio)} bytes (WAV, 1s of 440Hz tone)")

    status = FakeStatusMsg()
    print("Calling transcribe_audio() through Aporto...")
    result = await transcribe_audio(audio, "FLASH", status, "EN")
    if result is None:
        print(f"FAILED. Last status text: {status.last_text!r}")
        sys.exit(2)
    print(f"OK. Transcribed text: {result!r}")


if __name__ == "__main__":
    asyncio.run(main())
