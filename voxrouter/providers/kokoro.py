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
    ("jf_alpha", "ja-JP", "Female"), ("jf_daisuke", "ja-JP", "Male"),
    ("zf_xiaobei", "zh-CN", "Female"), ("zm_yunjian", "zh-CN", "Male"),
    ("hf_alpha", "hi-IN", "Female"), ("zm_yunxi", "zh-CN", "Male"),
]
