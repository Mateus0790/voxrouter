"""Provedor de teste: gera um tom senoidal WAV (sem rede). options.fail=true força erro."""

from __future__ import annotations

import asyncio
import io
import math
import struct
import time
import wave

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult


class MockProvider(BaseProvider):
    type = "mock"
    direct_as_voice = False

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        if ctx.model == "fail" or ctx.options.get("fail"):
            raise ProviderError(self.name, "falha simulada (options.fail=true)")
        delay = float(ctx.options.get("delay", 0.0))
        if delay:
            await asyncio.sleep(delay)
        t0 = time.monotonic()
        freq = {"tone1": 440.0, "tone2": 660.0, "tone3": 880.0}.get(ctx.model or "tone1", 440.0)
        rate = 24000
        duration = min(10.0, 0.35 + 0.05 * max(1, len(req.text.split())))
        n = int(duration * rate)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            frames = bytearray()
            for i in range(n):
                frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
            w.writeframes(bytes(frames))
        return SynthesisResult(
            audio=buf.getvalue(),
            format="wav",
            provider=self.name,
            model=ctx.model or "mock",
            voice=ctx.voice or "mock",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        return [
            {"voice": "tone1", "locale": "mock", "gender": None, "label": "Tom 440 Hz", "provider": self.name},
            {"voice": "tone2", "locale": "mock", "gender": None, "label": "Tom 660 Hz", "provider": self.name},
            {"voice": "tone3", "locale": "mock", "gender": None, "label": "Tom 880 Hz", "provider": self.name},
        ]
