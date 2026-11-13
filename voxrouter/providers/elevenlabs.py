"""ElevenLabs — TTS (com streaming), STT (Scribe) e uso real da assinatura.
Sintaxe direta: 'elevenlabs/<modelo>' (eleven_*/turbo*) ou 'elevenlabs/<id-da-voz>'."""

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

DEFAULT_BASE = "https://api.elevenlabs.io"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (voz clássica)


class ElevenLabsProvider(BaseProvider):
    type = "elevenlabs"
    capabilities = frozenset({"tts", "stt"})
    direct_as_voice = False  # resolvido por heurística no roteador
    default_max_chars = 9500  # eleven_multilingual_v2 aceita 10k; margem de segurança

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def output_format(self, req: SynthesisRequest) -> str:
        return "pcm" if req.response_format == "pcm" else "mp3"

    def _request(self, req: SynthesisRequest, ctx: ProviderContext) -> tuple[str, str, dict, dict]:
        model_id = ctx.model or self.cfg.options.get("model", "eleven_multilingual_v2")
        voice_id = ctx.voice or req.voice or self.cfg.options.get("voice_id", DEFAULT_VOICE_ID)
        fmt = "pcm_24000" if self.output_format(req) == "pcm" else "mp3_44100_128"
        settings: dict = {
            "stability": float(self.cfg.options.get("stability", 0.5)),
            "similarity_boost": float(self.cfg.options.get("similarity_boost", 0.75)),
            "use_speaker_boost": bool(self.cfg.options.get("use_speaker_boost", True)),
        }
        if req.speed != 1.0:
            settings["speed"] = max(0.7, min(1.2, req.speed))
        body = {"text": req.text, "model_id": model_id, "voice_settings": settings}
        return voice_id, model_id, {"output_format": fmt}, body

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.require_key()
        voice_id, model_id, params, body = self._request(req, ctx)
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{self.base}/v1/text-to-speech/{voice_id}", params=params, json=body, headers={"xi-api-key": key}
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"ElevenLabs {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=self.output_format(req),
            provider=self.name,
            model=model_id,
            voice=voice_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        key = self.require_key()
        voice_id, _, params, body = self._request(req, ctx)
        async for chunk in self._http_stream(
            "POST", f"{self.base}/v1/text-to-speech/{voice_id}/stream", "ElevenLabs",
            params=params, json=body, headers={"xi-api-key": key},
        ):
            yield chunk

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        key = self.require_key()
        model = ctx.model or self.cfg.options.get("stt_model", "scribe_v2")
        data: dict = {"model_id": model}
        lang = short_lang(req.language)
        if lang:
            data["language_code"] = lang
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{self.base}/v1/speech-to-text",
            data=data,
            files={"file": (req.filename, req.audio, req.content_type)},
            headers={"xi-api-key": key},
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"ElevenLabs STT {resp.status_code}: {resp.text[:300]}", resp.status_code)
        body = resp.json()
        return TranscriptionResult(
            text=(body.get("text") or "").strip(),
            provider=self.name,
            model=model,
            language=body.get("language_code") or lang,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def remote_usage(self) -> dict | None:
        """GET /v1/user/subscription -> character_count / character_limit."""
        key = self.api_key()
        if not key:
            return None
        resp = await self.client.get(f"{self.base}/v1/user/subscription", headers={"xi-api-key": key})
        if resp.status_code != 200:
            raise ProviderError(self.name, f"ElevenLabs subscription {resp.status_code}: {resp.text[:200]}")
        s = resp.json()
        return {
            "used": int(s["character_count"]),
            "limit": s.get("character_limit"),
            "reset_unix": s.get("next_character_count_reset_unix"),
            "tier": s.get("tier"),
        }

    async def list_voices(self) -> list[dict]:
        """Lista as vozes da sua conta (exige chave; [] se ausente ou com erro)."""
        key = self.api_key()
        if not key:
            return []
        try:
            resp = await self.client.get(f"{self.base}/v1/voices", headers={"xi-api-key": key})
            if resp.status_code != 200:
                return []
            out = []
            for v in resp.json().get("voices", []):
                labels = v.get("labels") or {}
                out.append(
                    {
                        "voice": v.get("voice_id"),
                        "label": v.get("name") or v.get("voice_id"),
                        "locale": labels.get("language"),
                        "gender": labels.get("gender"),
                        "provider": self.name,
                    }
                )
            return out
        except Exception:
            return []
