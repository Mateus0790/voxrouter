"""OmniVoice local (servidor OpenAI-compatível em http://localhost:6655).
Campos extras (ref_audio, ref_text, instruct) são repassados: clonagem e design de voz."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

DEFAULT_BASE = "http://localhost:6655"


class OmniVoiceProvider(BaseProvider):
    type = "omnivoice"
    direct_as_voice = False

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        payload: dict = {
            "model": ctx.model or self.cfg.options.get("model", "omnivoice"),
            "input": req.text,
            "response_format": req.response_format,
        }
        voice = ctx.voice or req.voice
        if voice:
            payload["voice"] = voice
        payload.update(req.extra)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/audio/speech", json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"OmniVoice {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=req.response_format,
            provider=self.name,
            model=payload["model"],
            voice=voice or "",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
