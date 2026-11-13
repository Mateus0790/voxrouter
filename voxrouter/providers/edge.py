"""Microsoft Edge TTS — grátis, sem chave de API, com streaming nativo.
Vozes: pt-BR-AntonioNeural, pt-BR-FranciscaNeural, ..."""

from __future__ import annotations

import time
from typing import AsyncIterator

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult


class EdgeProvider(BaseProvider):
    type = "edge"
    direct_as_voice = True

    def output_format(self, req: SynthesisRequest) -> str:
        return "mp3"

    def _voice(self, req: SynthesisRequest, ctx: ProviderContext) -> str:
        return ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-AntonioNeural")

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        import edge_tts  # import tardio

        voice = self._voice(req, ctx)
        rate_pct = max(-50, min(100, int(round((req.speed - 1.0) * 100))))
        got = False
        try:
            com = edge_tts.Communicate(req.text, voice, rate=f"{rate_pct:+d}%")
            async for chunk in com.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    got = True
                    yield chunk.get("data", b"")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"edge-tts: {exc}") from exc
        if not got:
            raise ProviderError(self.name, "edge-tts retornou áudio vazio")

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        t0 = time.monotonic()
        audio = b"".join([c async for c in self.stream(req, ctx)])
        return SynthesisResult(
            audio=audio,
            format="mp3",
            provider=self.name,
            model="edge-tts",
            voice=self._voice(req, ctx),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def list_voices(self) -> list[dict]:
        """Catálogo ao vivo da Microsoft (cache 1h); fallback estático se offline."""
        now = time.monotonic()
        cached = getattr(self, "_voices", None)
        if cached is not None and now - cached[0] < 3600:
            return cached[1]
        try:
            import edge_tts

            raw = await edge_tts.list_voices()
            out = [
                {
                    "voice": v["ShortName"],
                    "locale": v.get("Locale"),
                    "gender": v.get("Gender"),
                    "label": v.get("FriendlyName") or v["ShortName"],
                    "provider": self.name,
                }
                for v in raw
                if v.get("ShortName")
            ]
        except Exception:
            out = [{**v, "provider": self.name} for v in EDGE_FALLBACK]
        self._voices = (now, out)
        return out


EDGE_FALLBACK = [
    {"voice": "pt-BR-AntonioNeural", "locale": "pt-BR", "gender": "Male", "label": "Microsoft Antonio (pt-BR)"},
    {"voice": "pt-BR-FranciscaNeural", "locale": "pt-BR", "gender": "Female", "label": "Microsoft Francisca (pt-BR)"},
    {"voice": "pt-BR-ThalitaNeural", "locale": "pt-BR", "gender": "Female", "label": "Microsoft Thalita (pt-BR)"},
    {"voice": "en-US-AriaNeural", "locale": "en-US", "gender": "Female", "label": "Microsoft Aria (en-US)"},
    {"voice": "en-US-GuyNeural", "locale": "en-US", "gender": "Male", "label": "Microsoft Guy (en-US)"},
    {"voice": "en-US-JennyNeural", "locale": "en-US", "gender": "Female", "label": "Microsoft Jenny (en-US)"},
    {"voice": "es-ES-AlvaroNeural", "locale": "es-ES", "gender": "Male", "label": "Microsoft Álvaro (es-ES)"},
    {"voice": "es-ES-ElviraNeural", "locale": "es-ES", "gender": "Female", "label": "Microsoft Elvira (es-ES)"},
]
