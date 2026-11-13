"""Kokoro local (kokoro-fastapi em http://localhost:8880, endpoint OpenAI-compatível)."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

SUPPORTED = ("mp3", "wav", "opus", "flac", "pcm")


class KokoroProvider(BaseProvider):
    type = "kokoro"
    direct_as_voice = True

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "af_heart")
        payload: dict = {
            "model": ctx.model or self.cfg.options.get("model", "kokoro"),
            "input": req.text,
            "voice": voice,
            "response_format": req.response_format if req.response_format in SUPPORTED else "mp3",
            "speed": req.speed,
        }
        payload.update(req.extra)  # ex.: lang_code
        t0 = time.monotonic()
        resp = await self.client.post("/v1/audio/speech", json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Kokoro {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=payload["response_format"],
            provider=self.name,
            model=payload["model"],
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
