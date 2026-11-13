"""Azure Speech (TTS neural via SSML). Vozes pt-BR: pt-BR-FranciscaNeural, pt-BR-AntonioNeural, ..."""

from __future__ import annotations

import html
import os
import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

FORMATS = {
    "mp3": "audio-24khz-48kbitrate-mono-mp3",
    "wav": "riff-24khz-16bit-mono-pcm",
    "opus": "audio-24khz-16kbitrate-mono-opus",
    "ogg": "ogg-24khz-16bit-mono-opus",
    "pcm": "raw-24khz-16bit-mono-pcm",
}

RESULT_FMT = {"wav": "wav", "opus": "ogg", "ogg": "ogg", "pcm": "pcm"}


class AzureProvider(BaseProvider):
    type = "azure"
    direct_as_voice = True

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.api_key()
        region = self.cfg.options.get("region") or os.environ.get("AZURE_SPEECH_REGION")
        if not key or not region:
            raise ProviderError(self.name, "faltam AZURE_SPEECH_KEY e AZURE_SPEECH_REGION (ou options.region)")
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-FranciscaNeural")
        lang = req.language or self.cfg.options.get("language", "pt-BR")
        out_fmt = FORMATS.get(req.response_format, "audio-24khz-48kbitrate-mono-mp3")
        rate_pct = int(round((req.speed - 1.0) * 100))
        ssml = (
            f'<speak version="1.0" xml:lang="{lang}">'
            f'<voice name="{voice}"><prosody rate="{rate_pct:+d}%">{html.escape(req.text)}</prosody></voice></speak>'
        )
        base = self.cfg.base_url or f"https://{region}.tts.speech.microsoft.com"
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{base}/cognitiveservices/v1",
            content=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": out_fmt,
            },
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Azure {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=RESULT_FMT.get(req.response_format, "mp3"),
            provider=self.name,
            model=voice,
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
