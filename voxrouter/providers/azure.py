"""Azure Speech (TTS neural via SSML). Vozes pt-BR: pt-BR-FranciscaNeural, pt-BR-AntonioNeural, ..."""

from __future__ import annotations

import html
import os
import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

FORMATS = {
    "mp3": "audio-24khz-48kbitrate-mono-mp3",
    "wav": "riff-24khz-16bit-mono-pcm",
    "opus": "ogg-24khz-16bit-mono-opus",
    "pcm": "raw-24khz-16bit-mono-pcm",
}


class AzureProvider(BaseProvider):
    type = "azure"
    direct_as_voice = True
    default_max_chars = 5000

    def output_format(self, req: SynthesisRequest) -> str:
        return req.response_format if req.response_format in FORMATS else "mp3"

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.api_key()
        region = self.cfg.options.get("region") or os.environ.get("AZURE_SPEECH_REGION")
        if not key or not region:
            raise ProviderError(self.name, "faltam AZURE_SPEECH_KEY e AZURE_SPEECH_REGION (ou options.region)")
        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-FranciscaNeural")
        lang = req.language or self.cfg.options.get("language") or "-".join(voice.split("-")[:2])
        fmt = self.output_format(req)
        rate_pct = int(round((req.speed - 1.0) * 100))
        ssml = (
            f'<speak version="1.0" xml:lang="{lang}">'
            f'<voice name="{voice}"><prosody rate="{rate_pct:+d}%">{html.escape(req.text)}</prosody></voice></speak>'
        )
        base = (self.cfg.base_url or f"https://{region}.tts.speech.microsoft.com").rstrip("/")
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{base}/cognitiveservices/v1",
            content=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": FORMATS[fmt],
            },
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"Azure {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format=fmt,
            provider=self.name,
            model=voice,
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        """Catálogo estático curado (lista completa: ~500 vozes, exige chave)."""
        return [
            {"voice": v, "locale": loc, "gender": g, "label": f"Azure {v}", "provider": self.name}
            for v, loc, g in AZURE_VOICES
        ]


AZURE_VOICES = [
    ("pt-BR-FranciscaNeural", "pt-BR", "Female"), ("pt-BR-AntonioNeural", "pt-BR", "Male"),
    ("pt-BR-ThalitaNeural", "pt-BR", "Female"), ("pt-BR-BrendaNeural", "pt-BR", "Female"),
    ("pt-BR-DonatoNeural", "pt-BR", "Male"), ("pt-BR-FabioNeural", "pt-BR", "Male"),
    ("en-US-JennyNeural", "en-US", "Female"), ("en-US-GuyNeural", "en-US", "Male"),
    ("en-US-AriaNeural", "en-US", "Female"), ("en-US-DavisNeural", "en-US", "Male"),
    ("es-ES-ElviraNeural", "es-ES", "Female"), ("es-ES-AlvaroNeural", "es-ES", "Male"),
    ("fr-FR-DeniseNeural", "fr-FR", "Female"), ("fr-FR-HenriNeural", "fr-FR", "Male"),
    ("de-DE-KatjaNeural", "de-DE", "Female"), ("de-DE-ConradNeural", "de-DE", "Male"),
    ("it-IT-ElsaNeural", "it-IT", "Female"), ("it-IT-DiegoNeural", "it-IT", "Male"),
    ("ja-JP-NanamiNeural", "ja-JP", "Female"), ("ja-JP-KeitaNeural", "ja-JP", "Male"),
    ("zh-CN-XiaoxiaoNeural", "zh-CN", "Female"), ("zh-CN-YunxiNeural", "zh-CN", "Male"),
    ("ko-KR-SunHiNeural", "ko-KR", "Female"), ("ko-KR-InJoonNeural", "ko-KR", "Male"),
]
