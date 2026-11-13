"""Fish Audio — TTS (POST /v1/tts), clonagem via modelos de voz (reference_id).

Conforme a referência oficial (docs.fish.audio): auth Bearer, header
`model` (s2.1-pro, s2.1-pro-free p/ o plano gratuito, s1, ...), corpo JSON
{text, reference_id, format: wav|pcm|mp3|opus, latency, prosody{speed}}.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

DEFAULT_BASE = "https://api.fish.audio"
FORMATS = ("mp3", "wav", "pcm", "opus")


class FishAudioProvider(BaseProvider):
    type = "fish"
    direct_as_voice = True  # 'fish/<reference_id>'

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in FORMATS else "mp3"

    def _request(self, req: SynthesisRequest, ctx: ProviderContext) -> tuple[dict, dict]:
        key = self.require_key()
        opts = self.cfg.options
        fmt = self.output_format(req)
        body: dict = {
            "text": req.text,
            "format": fmt,
            "latency": opts.get("latency", "normal"),
            "normalize": bool(opts.get("normalize", True)),
        }
        voice = ctx.voice or req.voice or opts.get("voice")
        if voice:
            body["reference_id"] = voice
        if fmt == "mp3":
            body["mp3_bitrate"] = int(opts.get("mp3_bitrate", 128))
        if req.speed != 1.0:
            body["prosody"] = {"speed": req.speed}
        headers = {
            "Authorization": f"Bearer {key}",
            "model": ctx.model or opts.get("model", "s2.1-pro"),
        }
        return body, headers

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        body, headers = self._request(req, ctx)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/tts", json=body, headers=headers)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Fish Audio {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=body["format"],
            provider=self.name,
            model=headers["model"],
            voice=body.get("reference_id", ""),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        body, headers = self._request(req, ctx)
        async for chunk in self._http_stream("POST", f"{self.base}/v1/tts", "Fish Audio", json=body, headers=headers):
            yield chunk
