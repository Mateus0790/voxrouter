"""Provedor de teste (sem rede): TTS gera tom senoidal WAV; STT devolve texto fixo.

options:
  fail: true               -> toda chamada falha (testa failover)
  delay: 0.2               -> atraso antes de sintetizar
  stream_delay: 0.05       -> atraso entre pedaços no streaming
  stream_fail_after: 1     -> stream quebra depois de N pedaços
  transcript: "..."        -> texto devolvido pelo STT
  remote_used / remote_limit -> simula uso informado pelo provedor (sync de cota)
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
import time
import wave
from typing import AsyncIterator

from .base import (
    BaseProvider,
    ProviderContext,
    ProviderError,
    SynthesisRequest,
    SynthesisResult,
    TranscriptionRequest,
    TranscriptionResult,
    short_lang,
)

FREQS = {"tone1": 440.0, "tone2": 660.0, "tone3": 880.0}


class MockProvider(BaseProvider):
    type = "mock"
    capabilities = frozenset({"tts", "stt"})
    direct_as_voice = False

    def output_format(self, req: SynthesisRequest) -> str:
        return "wav"

    def _check_fail(self, ctx: ProviderContext) -> None:
        if ctx.model == "fail" or ctx.options.get("fail"):
            raise ProviderError(self.name, "falha simulada (options.fail=true)")

    def _render(self, req: SynthesisRequest, ctx: ProviderContext) -> bytes:
        freq = FREQS.get(ctx.model or ctx.voice or "tone1", 440.0)
        rate = 24000
        duration = min(10.0, 0.35 + 0.05 * max(1, len(req.text.split())))
        n = int(duration * rate)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(
                b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))
            )
        return buf.getvalue()

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        self._check_fail(ctx)
        delay = float(ctx.options.get("delay", 0.0))
        if delay:
            await asyncio.sleep(delay)
        t0 = time.monotonic()
        audio = self._render(req, ctx)
        return SynthesisResult(
            audio=audio,
            format="wav",
            provider=self.name,
            model=ctx.model or "mock",
            voice=ctx.voice or "mock",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        self._check_fail(ctx)
        audio = self._render(req, ctx)
        step = max(1, len(audio) // 3 + 1)
        fail_after = ctx.options.get("stream_fail_after")
        sdelay = float(ctx.options.get("stream_delay", 0.0))
        for i, start in enumerate(range(0, len(audio), step)):
            if fail_after is not None and i >= int(fail_after):
                raise ProviderError(self.name, "stream interrompido (simulado)")
            if sdelay:
                await asyncio.sleep(sdelay)
            yield audio[start:start + step]

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        self._check_fail(ctx)
        text = ctx.options.get("transcript") or f"transcrição mock de {len(req.audio)} bytes"
        return TranscriptionResult(
            text=text,
            provider=self.name,
            model=ctx.model or "mock-stt",
            language=short_lang(req.language) or "pt",
            duration=round(len(req.audio) / 48000, 2),
        )

    async def remote_usage(self) -> dict | None:
        if "remote_used" not in self.cfg.options:
            return None
        return {"used": int(self.cfg.options["remote_used"]), "limit": self.cfg.options.get("remote_limit")}

    async def list_voices(self) -> list[dict]:
        return [
            {"voice": v, "locale": "mock", "gender": None, "label": f"Tom {int(f)} Hz", "provider": self.name}
            for v, f in FREQS.items()
        ]
