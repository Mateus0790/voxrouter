"""OpenAI Audio API — TTS (tts-1, tts-1-hd, gpt-4o-mini-tts) e STT (whisper-1,
gpt-4o-transcribe). Também serve qualquer servidor OpenAI-compatível (Groq,
LocalAI, wrappers de F5-TTS etc.): basta `base_url` (chave opcional)."""

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

DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(BaseProvider):
    type = "openai"
    capabilities = frozenset({"tts", "stt"})
    direct_as_voice = False
    default_max_chars = 4096

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        if self.cfg.api_key_env:  # chave exigida só quando configurada (servidores locais não precisam)
            return {"Authorization": f"Bearer {self.require_key()}"}
        return {}

    def _payload(self, req: SynthesisRequest, ctx: ProviderContext) -> dict:
        payload: dict = {
            "model": ctx.model or self.cfg.options.get("model", "tts-1"),
            "input": req.text,
            "voice": ctx.voice or req.voice or self.cfg.options.get("voice", "alloy"),
            "response_format": req.response_format,
        }
        if req.speed != 1.0:
            payload["speed"] = max(0.25, min(4.0, req.speed))
        return payload

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        payload = self._payload(req, ctx)
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/audio/speech", json=payload, headers=self._headers())
        if resp.status_code != 200:
            raise ProviderError(self.name, f"OpenAI {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=req.response_format,
            provider=self.name,
            model=payload["model"],
            voice=payload["voice"],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        async for chunk in self._http_stream(
            "POST", f"{self.base}/audio/speech", "OpenAI",
            json=self._payload(req, ctx), headers=self._headers(),
        ):
            yield chunk

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        model = ctx.model or self.cfg.options.get("stt_model", "whisper-1")
        data: dict = {"model": model, "response_format": self.cfg.options.get("stt_response_format", "json")}
        lang = short_lang(req.language)
        if lang:
            data["language"] = lang
        if req.prompt:
            data["prompt"] = req.prompt
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{self.base}/audio/transcriptions",
            data=data,
            files={"file": (req.filename, req.audio, req.content_type)},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"OpenAI STT {resp.status_code}: {resp.text[:300]}", resp.status_code)
        body = resp.json()
        return TranscriptionResult(
            text=(body.get("text") or "").strip(),
            provider=self.name,
            model=model,
            language=body.get("language") or lang,
            duration=body.get("duration"),
            segments=[
                {"start": s.get("start"), "end": s.get("end"), "text": (s.get("text") or "").strip()}
                for s in body.get("segments") or []
            ],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        return [
            {"voice": v, "locale": None, "gender": None, "label": v.capitalize(), "provider": self.name}
            for v in OPENAI_VOICES
        ]


OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
