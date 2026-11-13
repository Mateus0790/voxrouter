"""Registro de provedores de voz do VoxRouter."""

from __future__ import annotations

from typing import Type

from ..config import ProviderConfig
from .base import BaseProvider, ProviderContext, ProviderError, SynthesisRequest, SynthesisResult
from .mock import MockProvider
from .edge import EdgeProvider
from .openai import OpenAIProvider
from .elevenlabs import ElevenLabsProvider
from .google import GoogleProvider
from .azure import AzureProvider
from .kokoro import KokoroProvider
from .omnivoice import OmniVoiceProvider

REGISTRY: dict[str, Type[BaseProvider]] = {
    "mock": MockProvider,
    "edge": EdgeProvider,
    "openai": OpenAIProvider,
    "elevenlabs": ElevenLabsProvider,
    "google": GoogleProvider,
    "azure": AzureProvider,
    "kokoro": KokoroProvider,
    "omnivoice": OmniVoiceProvider,
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
]
