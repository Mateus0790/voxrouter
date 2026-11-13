"""Microsoft Edge TTS — grátis, sem chave de API. Vozes: pt-BR-AntonioNeural, pt-BR-FranciscaNeural, ..."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult


class EdgeProvider(BaseProvider):
    type = "edge"
    direct_as_voice = True

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        import edge_tts  # import tardio

        voice = ctx.voice or req.voice or self.cfg.options.get("voice", "pt-BR-AntonioNeural")
        rate_pct = max(-50, min(100, int(round((req.speed - 1.0) * 100))))
        rate = f"{rate_pct:+d}%"
        t0 = time.monotonic()
        try:
            com = edge_tts.Communicate(req.text, voice, rate=rate)
            audio = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
        except Exception as exc:
            raise ProviderError(self.name, f"edge-tts: {exc}") from exc
        if not audio:
            raise ProviderError(self.name, "edge-tts retornou áudio vazio")
        return SynthesisResult(
            audio=bytes(audio),
            format="mp3",
            provider=self.name,
            model="edge-tts",
            voice=voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
