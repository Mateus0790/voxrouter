"""Deepgram — TTS Aura (POST /v1/speak) e STT Nova (POST /v1/listen).

Conforme a referência oficial (developers.deepgram.com): auth
`Authorization: Token <chave>`. No TTS a "voz" é o próprio modelo
(ex.: aura-2-thalia-en), passado em ?model=; corpo {"text": ...}.
No STT o áudio vai cru no corpo e a transcrição volta em
results.channels[0].alternatives[0].transcript.
"""

from __future__ import annotations

import time
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

DEFAULT_BASE = "https://api.deepgram.com"
ENCODINGS = {
    "mp3": {"encoding": "mp3"},
    "wav": {"encoding": "linear16", "container": "wav"},
    "pcm": {"encoding": "linear16", "container": "none"},
    "opus": {"encoding": "opus", "container": "ogg"},
    "flac": {"encoding": "flac"},
    "aac": {"encoding": "aac"},
}


class DeepgramProvider(BaseProvider):
    type = "deepgram"
    capabilities = frozenset({"tts", "stt"})
    direct_as_voice = True  # 'deepgram/aura-2-thalia-en'
    default_max_chars = 2000  # limite do /v1/speak por requisição

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.require_key()}"}

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in ENCODINGS else "mp3"

    def _params(self, req: SynthesisRequest, ctx: ProviderContext) -> dict:
        voice = ctx.voice or req.voice or ctx.model or self.cfg.options.get("voice", "aura-2-thalia-en")
        return {"model": voice, **ENCODINGS[self.output_format(req)]}

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        headers = self._headers()
        params = self._params(req, ctx)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/speak", params=params, json={"text": req.text}, headers=headers)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Deepgram {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=self.output_format(req),
            provider=self.name,
            model=params["model"],
            voice=params["model"],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        headers = self._headers()
        async for chunk in self._http_stream(
            "POST", f"{self.base}/v1/speak", "Deepgram",
            params=self._params(req, ctx), json={"text": req.text}, headers=headers,
        ):
            yield chunk

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        headers = {**self._headers(), "Content-Type": req.content_type or "application/octet-stream"}
        model = ctx.model or self.cfg.options.get("stt_model", "nova-3")
        params: dict = {"model": model, "smart_format": "true"}
        lang = short_lang(req.language)
        if lang:
            params["language"] = lang
        else:
            params["detect_language"] = "true"
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/listen", params=params, content=req.audio, headers=headers)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Deepgram STT {resp.status_code}: {resp.text[:300]}", resp.status_code)
        body = resp.json()
        try:
            channel = body["results"]["channels"][0]
            text = channel["alternatives"][0]["transcript"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(self.name, f"resposta inesperada do Deepgram: {exc}") from exc
        return TranscriptionResult(
            text=(text or "").strip(),
            provider=self.name,
            model=model,
            language=channel.get("detected_language") or lang,
            duration=(body.get("metadata") or {}).get("duration"),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
