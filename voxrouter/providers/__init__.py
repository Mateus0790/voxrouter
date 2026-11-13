"""Registro de provedores de voz do VoxRouter."""

from __future__ import annotations

from typing import Type

from ..config import ProviderConfig
from .base import (
    BaseProvider,
    ProviderContext,
    ProviderError,
    SynthesisRequest,
    SynthesisResult,
    TranscriptionRequest,
    TranscriptionResult,
)
from .azure import AzureProvider
from .cartesia import CartesiaProvider
from .deepgram import DeepgramProvider
from .edge import EdgeProvider
from .elevenlabs import ElevenLabsProvider
from .fish import FishAudioProvider
from .google import GoogleProvider
from .kokoro import KokoroProvider
from .mock import MockProvider
from .omnivoice import OmniVoiceProvider
from .openai import OpenAIProvider
from .whisper_local import WhisperLocalProvider

REGISTRY: dict[str, Type[BaseProvider]] = {
    "mock": MockProvider,
    "edge": EdgeProvider,
    "openai": OpenAIProvider,
    "elevenlabs": ElevenLabsProvider,
    "google": GoogleProvider,
    "azure": AzureProvider,
    "kokoro": KokoroProvider,
    "omnivoice": OmniVoiceProvider,
    "cartesia": CartesiaProvider,
    "deepgram": DeepgramProvider,
    "fish": FishAudioProvider,
    "whisper_local": WhisperLocalProvider,
}


def create_provider(name: str, cfg: ProviderConfig) -> BaseProvider:
    cls = REGISTRY.get(cfg.type)
    if cls is None:
        raise ValueError(
            f"tipo de provedor desconhecido '{cfg.type}' para '{name}'. Tipos: {sorted(REGISTRY)}"
        )
    return cls(name, cfg)


__all__ = [
    "REGISTRY",
    "create_provider",
    "BaseProvider",
    "ProviderContext",
    "ProviderError",
    "SynthesisRequest",
    "SynthesisResult",
    "TranscriptionRequest",
    "TranscriptionResult",
]
