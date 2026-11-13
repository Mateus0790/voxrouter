"""Interface comum de todos os provedores de voz (TTS e STT)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

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
    chunks: int = 1


@dataclass
class TranscriptionRequest:
    audio: bytes
    filename: str = "audio"
    content_type: str = "application/octet-stream"
    model: str | None = None
    language: str | None = None
    prompt: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptionResult:
    text: str
    provider: str
    model: str = ""
    language: str | None = None
    duration: float | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    cached: bool = False


@dataclass
class ProviderContext:
    """Overrides por candidato (vindos da rota ou da sintaxe direta)."""

    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def short_lang(language: str | None) -> str | None:
    """'pt-BR' -> 'pt' (formato ISO-639-1 que a maioria das APIs de STT espera)."""
    if not language:
        return None
    return language.split("-")[0].split("_")[0].lower()


class BaseProvider:
    """Contrato que todo provedor implementa (sobrescreva o que suportar)."""

    type: str = "base"
    #: capacidades: "tts" (síntese) e/ou "stt" (transcrição)
    capabilities: frozenset[str] = frozenset({"tts"})
    #: na sintaxe direta 'provedor/x' de TTS, o restante vira `voice` (senão `model`)
    direct_as_voice: bool = False
    #: limite de caracteres por chamada; textos maiores são divididos (None = sem limite)
    default_max_chars: int | None = None

    def __init__(self, name: str, cfg: ProviderConfig):
        self.name = name
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------- infra

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = {"timeout": httpx.Timeout(120.0, connect=10.0)}
            if self.cfg.base_url:
                kwargs["base_url"] = self.cfg.base_url
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    def api_key(self) -> str | None:
        env = self.cfg.api_key_env
        if not env:
            return None
        return os.environ.get(env) or self.cfg.options.get("api_key")

    def require_key(self) -> str:
        key = self.api_key()
        if not key:
            raise ProviderError(self.name, f"chave ausente: defina a variável {self.cfg.api_key_env}")
        return key

    @property
    def max_chars(self) -> int | None:
        return self.cfg.max_chars or self.default_max_chars

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _http_stream(self, method: str, url: str, label: str, **kwargs: Any) -> AsyncIterator[bytes]:
        """Faz a requisição em streaming; erro HTTP vira ProviderError no 1º chunk."""
        async with self.client.stream(method, url, **kwargs) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:300].decode("utf-8", "replace")
                raise ProviderError(self.name, f"{label} {resp.status_code}: {body}", resp.status_code)
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    # --------------------------------------------------------------- TTS

    def output_format(self, req: SynthesisRequest) -> str:
        """Formato que o provedor realmente devolve para este pedido."""
        return req.response_format

    async def synthesize(self, req: SynthesisRequest, ctx: ProviderContext) -> SynthesisResult:
        raise ProviderError(self.name, f"provedor '{self.type}' não suporta síntese (TTS)")

    async def stream(self, req: SynthesisRequest, ctx: ProviderContext) -> AsyncIterator[bytes]:
        """Áudio em pedaços. Padrão: sintetiza tudo e entrega de uma vez."""
        res = await self.synthesize(req, ctx)
        yield res.audio

    async def list_voices(self) -> list[dict[str, Any]]:
        """Catálogo de vozes do provedor (alimenta GET /v1/voices)."""
        return []

    # --------------------------------------------------------------- STT

    async def transcribe(self, req: TranscriptionRequest, ctx: ProviderContext) -> TranscriptionResult:
        raise ProviderError(self.name, f"provedor '{self.type}' não suporta transcrição (STT)")

    # ------------------------------------------------------------- cotas

    async def remote_usage(self) -> dict[str, Any] | None:
        """Uso real informado pela API do provedor: {'used': int, 'limit': int|None}."""
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "capabilities": sorted(self.capabilities),
            "base_url": self.cfg.base_url,
            "max_chars": self.max_chars,
            "key_configured": bool(self.api_key()) if self.cfg.api_key_env else None,
        }
