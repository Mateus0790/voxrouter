"""OmniVoice local (servidor OpenAI-compatível em http://localhost:6655)."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult


class OmniVoiceProvider(BaseProvider):
    type = "omnivoice"
    direct_as_voice = False

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        payload: dict = {
            "model": ctx.model or self.cfg.options.get("model", "omnivoice"),
            "input": req.text,
            "response_format": req.response_format,
        }
        if req.voice:
            payload["voice"] = req.voice
        payload.update(req.extra)  # ex.: ref_audio, ref_text, instruct (clonagem/design de voz)
        t0 = time.monotonic()
        resp = await self.client.post("/v1/audio/speech", json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"OmniVoice {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=req.response_format,
            provider=self.name,
            model=payload["model"],
            voice=req.voice or "",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
