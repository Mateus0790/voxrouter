"""Interface comum de todos os provedores de voz."""

from __future__ import annotations

import abc
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import ProviderConfig


class ProviderError(Exception):
    """Falha de um provedor (motiva failover no roteador)."""

    def __init__(self, provider: str, message: str, status: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status = status


@dataclass
class SynthesisRequest:
    """Pedido de síntese normalizado (espelho do payload da API)."""

    text: str
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    response_format: str = "mp3"
    speed: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesisResult:
    audio: bytes
    format: str
    provider: str
    model: str = ""
    voice: str = ""
    latency_ms: int = 0
    cached: bool = False


@dataclass
class ProviderContext:
    """Overrides por candidato (vindos da rota ou da sintaxe direta)."""

    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


class BaseProvider(abc.ABC):
    """Contrato que todo provedor deve implementar."""

    type: str = "base"
    #: na sintaxe direta 'provedor/x', o restante vira `voice` (senão vira `model`)
    direct_as_voice: bool = False

    def __init__(self, name: str, cfg: ProviderConfig):
        self.name = name
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.cfg.base_url, timeout=60.0)
        return self._client

    def api_key(self) -> str | None:
        env = self.cfg.api_key_env
        if not env:
            return None
        return os.environ.get(env) or self.cfg.options.get("api_key")

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    @abc.abstractmethod
    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        """Gera áudio. Deve lançar ProviderError em caso de falha."""

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "base_url": self.cfg.base_url,
            "key_configured": bool(self.api_key()) if self.cfg.api_key_env else None,
        }
