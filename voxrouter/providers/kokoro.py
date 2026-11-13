"""Kokoro local (kokoro-fastapi em http://localhost:8880, endpoint OpenAI-compatível)."""

from __future__ import annotations

import time
from typing import AsyncIterator

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

DEFAULT_BASE = "http://localhost:8880"
SUPPORTED = ("mp3", "wav", "opus", "flac", "pcm")


class KokoroProvider(BaseProvider):
    type = "kokoro"
    direct_as_voice = True

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in SUPPORTED else "mp3"

    def _payload(self, req: SynthesisRequest, ctx: ProviderContext, stream: bool) -> dict:
        payload: dict = {
            "model": ctx.model or self.cfg.options.get("model", "kokoro"),
            "input": req.text,
            "voice": ctx.voice or req.voice or self.cfg.options.get("voice", "af_heart"),
            "response_format": self.output_format(req),
            "speed": req.speed,
            "stream": stream,
        }
        payload.update(req.extra)  # ex.: lang_code
        return payload

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        payload = self._payload(req, ctx, stream=False)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/audio/speech", json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Kokoro {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=payload["response_format"],
            provider=self.name,
            model=payload["model"],
            voice=payload["voice"],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        async for chunk in self._http_stream(
            "POST", f"{self.base}/v1/audio/speech", "Kokoro", json=self._payload(req, ctx, stream=True)
        ):
            yield chunk

    async def list_voices(self) -> list[dict]:
        return [
            {"voice": v, "locale": loc, "gender": g, "label": f"Kokoro {v}", "provider": self.name}
            for v, loc, g in KOKORO_VOICES
        ]


# prefixos Kokoro: af_=americano F, am_=americano M, bf_=britânico F, bm_=britânico M, etc.
KOKORO_VOICES = [
    ("af_heart", "en-US", "Female"), ("af_alloy", "en-US", "Female"),
    ("af_bella", "en-US", "Female"), ("af_nicole", "en-US", "Female"),
    ("af_nova", "en-US", "Female"), ("af_sarah", "en-US", "Female"),
    ("af_sky", "en-US", "Female"), ("am_adam", "en-US", "Male"),
    ("am_michael", "en-US", "Male"), ("am_onyx", "en-US", "Male"),
    ("bf_alice", "en-GB", "Female"), ("bf_emma", "en-GB", "Female"),
    ("bf_lily", "en-GB", "Female"), ("bm_daniel", "en-GB", "Male"),
    ("bm_george", "en-GB", "Male"), ("bm_lewis", "en-GB", "Male"),
    ("ef_dora", "es-ES", "Female"), ("em_santa", "es-ES", "Male"),
    ("ff_siwis", "fr-FR", "Female"), ("if_sara", "it-IT", "Female"),
    ("jf_alpha", "ja-JP", "Female"), ("zf_xiaobei", "zh-CN", "Female"),
    ("zm_yunjian", "zh-CN", "Male"), ("zm_yunxi", "zh-CN", "Male"),
    ("hf_alpha", "hi-IN", "Female"), ("pf_dora", "pt-BR", "Female"),
    ("pm_alex", "pt-BR", "Male"), ("pm_santa", "pt-BR", "Male"),
]
