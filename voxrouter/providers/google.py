"""Google Cloud Text-to-Speech via REST (chave de API GOOGLE_TTS_API_KEY)."""

from __future__ import annotations

import base64
import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

ENCODINGS = {"mp3": "MP3", "wav": "LINEAR16", "opus": "OGG_OPUS", "flac": "FLAC", "pcm": "LINEAR16"}


class GoogleProvider(BaseProvider):
    type = "google"
    direct_as_voice = True

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.api_key()
        if not key:
            raise ProviderError(self.name, "chave ausente: defina GOOGLE_TTS_API_KEY (ou api_key_env)")
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-Neural2-C")
        lang = req.language or self.cfg.options.get("language_code", "pt-BR")
        encoding = ENCODINGS.get(req.response_format, "MP3")
        payload = {
            "input": {"text": req.text},
            "voice": {"languageCode": lang, "name": voice},
            "audioConfig": {"audioEncoding": encoding, "speakingRate": req.speed},
        }
        t0 = time.monotonic()
        resp = await self.client.post("/v1/text:synthesize", params={"key": key}, json=payload)
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Google TTS {resp.status_code}: {resp.text[:300]}", resp.status_code)
        audio = base64.b64decode(resp.json()["audioContent"])
        fmt = req.response_format if req.response_format in ENCODINGS else "mp3"
        return SynthesisResult(
            audio=audio,
            format=fmt,
            provider=self.name,
            model=voice,
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
