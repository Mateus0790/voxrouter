"""Whisper local via faster-whisper — STT grátis, offline, sem chave.

Instale com: pip install "voxrouter[local-stt]"
options: model (tiny|base|small|medium|large-v3), device (cpu|cuda), compute_type (int8|float16)
O modelo é baixado do Hugging Face na primeira chamada e fica em memória.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Any

from .base import (
    BaseProvider,
    ProviderContext,
    ProviderError,
    TranscriptionRequest,
    TranscriptionResult,
    short_lang,
)

_MODELS: dict[tuple[str, str, str], Any] = {}
_LOCK = threading.Lock()


class WhisperLocalProvider(BaseProvider):
    type = "whisper_local"
    capabilities = frozenset({"stt"})

    def _model(self, size: str) -> Any:
        from faster_whisper import WhisperModel

        device = str(self.cfg.options.get("device", "cpu"))
        compute = str(self.cfg.options.get("compute_type", "int8"))
        key = (size, device, compute)
        with _LOCK:
            if key not in _MODELS:
                _MODELS[key] = WhisperModel(size, device=device, compute_type=compute)
            return _MODELS[key]

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raise ProviderError(self.name, "faster-whisper não instalado: pip install \"voxrouter[local-stt]\"")
        size = ctx.model or self.cfg.options.get("model", "base")
        lang = short_lang(req.language)

        def run() -> tuple[str, str | None, float | None, list[dict]]:
            model = self._model(size)
            segments, info = model.transcribe(
                io.BytesIO(req.audio), language=lang, initial_prompt=req.prompt, beam_size=5
            )
            segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
            text = " ".join(s["text"] for s in segs).strip()
            return text, info.language, round(info.duration, 2), segs

        t0 = time.monotonic()
        try:
            text, detected, duration, segs = await asyncio.to_thread(run)
        except Exception as exc:
            raise ProviderError(self.name, f"faster-whisper: {type(exc).__name__}: {exc}") from exc
        return TranscriptionResult(
            text=text,
            provider=self.name,
            model=f"whisper-{size}",
            language=detected,
            duration=duration,
            segments=segs,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
