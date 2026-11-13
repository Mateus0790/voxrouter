"""Cartesia Sonic — TTS de baixa latência (POST /tts/bytes, com streaming).

Conforme a referência oficial (docs.cartesia.ai/api-reference/tts/bytes):
auth Bearer, header Cartesia-Version, corpo {model_id, transcript, voice,
output_format{container, ...}, language}. A voz é um ID (UUID) da sua conta
ou da biblioteca — não há voz padrão segura, então defina `options.voice`.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult, short_lang

DEFAULT_BASE = "https://api.cartesia.ai"
API_VERSION = "2026-08-14"


class CartesiaProvider(BaseProvider):
    type = "cartesia"
    direct_as_voice = True  # 'cartesia/<id-da-voz>'

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in ("mp3", "wav", "pcm") else "mp3"

    def _request(self, req: SynthesisRequest, ctx: ProviderContext) -> tuple[dict, dict]:
        key = self.require_key()
        opts = self.cfg.options
        voice = ctx.voice or req.voice or opts.get("voice")
        if not voice:
            raise ProviderError(self.name, "defina a voz Cartesia (ID) em options.voice ou no campo 'voice'")
        fmt = self.output_format(req)
        if fmt == "mp3":
            of = {"container": "mp3", "sample_rate": int(opts.get("sample_rate", 44100)),
                  "bit_rate": int(opts.get("bit_rate", 128000))}
        elif fmt == "wav":
            of = {"container": "wav", "encoding": "pcm_s16le", "sample_rate": int(opts.get("sample_rate", 44100))}
        else:
            of = {"container": "raw", "encoding": "pcm_s16le", "sample_rate": int(opts.get("sample_rate", 24000))}
        body: dict = {
            "model_id": ctx.model or opts.get("model", "sonic-3.6"),
            "transcript": req.text,
            "voice": voice,
            "output_format": of,
        }
        lang = short_lang(req.language) or opts.get("language")
        if lang:
            body["language"] = lang
        headers = {
            "Authorization": f"Bearer {key}",
            "Cartesia-Version": str(opts.get("version", API_VERSION)),
        }
        return body, headers

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        body, headers = self._request(req, ctx)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/tts/bytes", json=body, headers=headers)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Cartesia {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=self.output_format(req),
            provider=self.name,
            model=body["model_id"],
            voice=body["voice"],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        body, headers = self._request(req, ctx)
        async for chunk in self._http_stream("POST", f"{self.base}/tts/bytes", "Cartesia", json=body, headers=headers):
            yield chunk
