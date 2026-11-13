"""OpenAI Audio API (tts-1, tts-1-hd, gpt-4o-mini-tts)."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult


class OpenAIProvider(BaseProvider):
    type = "openai"
    direct_as_voice = False

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.api_key()
        if not key:
            raise ProviderError(self.name, "chave ausente: defina OPENAI_API_KEY (ou api_key_env)")
        model = ctx.model or self.cfg.options.get("model", "tts-1")
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "alloy")
        payload: dict = {
            "model": model,
            "input": req.text,
            "voice": voice,
            "response_format": req.response_format,
        }
        if req.speed != 1.0:
            payload["speed"] = max(0.25, min(4.0, req.speed))
        t0 = time.monotonic()
        resp = await self.client.post(
            "/audio/speech", json=payload, headers={"Authorization": f"Bearer {key}"}
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"OpenAI {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=req.response_format,
            provider=self.name,
            model=model,
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        return [
            {"voice": v, "locale": None, "gender": None, "label": label, "provider": self.name}
            for v, label in OPENAI_VOICES
        ]


OPENAI_VOICES = [
    ("alloy", "Alloy"), ("ash", "Ash"), ("ballad", "Ballad"), ("coral", "Coral"),
    ("echo", "Echo"), ("fable", "Fable"), ("nova", "Nova"), ("onyx", "Onyx"),
    ("sage", "Sage"), ("shimmer", "Shimmer"), ("verse", "Verse"),
]
