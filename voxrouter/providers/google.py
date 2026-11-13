"""Google Cloud Text-to-Speech via REST (chave de API GOOGLE_TTS_API_KEY)."""

from __future__ import annotations

import base64
import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

DEFAULT_BASE = "https://texttospeech.googleapis.com"
ENCODINGS = {"mp3": "MP3", "wav": "LINEAR16", "opus": "OGG_OPUS", "flac": "FLAC", "pcm": "LINEAR16"}


class GoogleProvider(BaseProvider):
    type = "google"
    direct_as_voice = True
    default_max_chars = 2500  # limite da API é 5000 BYTES; acentos ocupam 2 bytes

    @property
    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in ENCODINGS else "mp3"

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.require_key()
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-Neural2-C")
        lang = req.language or self.cfg.options.get("language_code") or "-".join(voice.split("-")[:2])
        fmt = self.output_format(req)
        payload = {
            "input": {"text": req.text},
            "voice": {"languageCode": lang, "name": voice},
            "audioConfig": {"audioEncoding": ENCODINGS[fmt], "speakingRate": req.speed},
        }
        t0 = time.monotonic()
        resp = await self.client.post(f"{self.base}/v1/text:synthesize", params={"key": key}, json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Google TTS {resp.status_code}: {resp.text[:300]}", resp.status_code)
        audio = base64.b64decode(resp.json()["audioContent"])
        return SynthesisResult(
            audio=audio,
            format=fmt,
            provider=self.name,
            model=voice,
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        """Catálogo estático curado (a lista completa exige a API voices:list com chave)."""
        return [
            {"voice": v, "locale": loc, "gender": g, "label": f"Google {v}", "provider": self.name}
            for v, loc, g in GOOGLE_VOICES
        ]


GOOGLE_VOICES = [
    ("pt-BR-Neural2-A", "pt-BR", "Female"), ("pt-BR-Neural2-B", "pt-BR", "Male"),
    ("pt-BR-Neural2-C", "pt-BR", "Female"), ("pt-BR-Neural2-D", "pt-BR", "Male"),
    ("en-US-Neural2-A", "en-US", "Female"), ("en-US-Neural2-B", "en-US", "Male"),
    ("en-US-Neural2-C", "en-US", "Female"), ("en-US-Neural2-D", "en-US", "Male"),
    ("en-US-Neural2-F", "en-US", "Female"), ("en-US-Neural2-I", "en-US", "Male"),
    ("en-GB-Neural2-A", "en-GB", "Female"), ("en-GB-Neural2-B", "en-GB", "Male"),
    ("es-ES-Neural2-A", "es-ES", "Female"), ("es-ES-Neural2-B", "es-ES", "Male"),
    ("fr-FR-Neural2-A", "fr-FR", "Female"), ("fr-FR-Neural2-B", "fr-FR", "Male"),
    ("de-DE-Neural2-A", "de-DE", "Female"), ("de-DE-Neural2-B", "de-DE", "Male"),
    ("it-IT-Neural2-A", "it-IT", "Female"), ("it-IT-Neural2-C", "it-IT", "Male"),
    ("ja-JP-Neural2-B", "ja-JP", "Female"), ("ja-JP-Neural2-C", "ja-JP", "Male"),
    ("zh-CN-Neural2-A", "zh-CN", "Female"), ("zh-CN-Neural2-B", "zh-CN", "Male"),
]
