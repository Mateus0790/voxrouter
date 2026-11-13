"""ElevenLabs TTS. Sintaxe direta: 'elevenlabs/<modelo>' ou 'elevenlabs/<id-da-voz>'."""

from __future__ import annotations

import time

from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult

OUTPUT_FORMATS = {
    "mp3": "mp3_44100_128",
    "pcm": "pcm_24000",
    "opus": "mp3_44100_64",
    "wav": "mp3_44100_128",
}

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (voz clássica)


class ElevenLabsProvider(BaseProvider):
    type = "elevenlabs"
    direct_as_voice = False  # resolvido por heurística no roteador

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        key = self.api_key()
        if not key:
            raise ProviderError(self.name, "chave ausente: defina ELEVENLABS_API_KEY (ou api_key_env)")
        model_id = ctx.model or self.cfg.options.get("model", "eleven_multilingual_v2")
        voice_id = ctx.voice or req.voice or self.cfg.options.get("voice_id", DEFAULT_VOICE_ID)
        fmt = OUTPUT_FORMATS.get(req.response_format, "mp3_44100_128")
        settings: dict = {
            "stability": float(self.cfg.options.get("stability", 0.5)),
            "similarity_boost": float(self.cfg.options.get("similarity_boost", 0.75)),
            "use_speaker_boost": bool(self.cfg.options.get("use_speaker_boost", True)),
        }
        if req.speed != 1.0:
            settings["speed"] = max(0.7, min(1.2, req.speed))
        body = {"text": req.text, "model_id": model_id, "voice_settings": settings}
        t0 = time.monotonic()
        resp = await self.client.post(
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": fmt},
            json=body,
            headers={"xi-api-key": key},
        )
        if resp.status_code != 200:
            raise ProviderError(self.name, f"ElevenLabs {resp.status_code}: {resp.text[:300]}", resp.status_code)
        return SynthesisResult(
            audio=resp.content,
            format="mp3" if req.response_format in ("wav", "opus") else req.response_format,
            provider=self.name,
            model=model_id,
            voice=voice_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
