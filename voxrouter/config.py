"""Modelos de configuração (YAML) do VoxRouter."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

STRATEGIES = ("first", "random", "round_robin", "weighted", "cheapest", "quota_aware")


class QuotaConfig(BaseModel):
    """Cota de caracteres por período (controle local, persistido em disco).

    Com `sync: true` o consumo (e o limite, se o provedor informar) é
    sincronizado periodicamente com a API do provedor (ex.: ElevenLabs).
    """

    limit: int | None = None
    period: str = "month"  # day | month
    reserve: float = 0.0  # fração final = status "low"
    sync: bool = False

    @model_validator(mode="after")
    def _check(self) -> "QuotaConfig":
        if self.limit is None and not self.sync:
            raise ValueError("quota.limit é obrigatório (exceto com sync: true)")
        if self.limit is not None and self.limit < 0:
            raise ValueError("quota.limit não pode ser negativo")
        if self.period not in ("day", "month"):
            raise ValueError(f"quota.period deve ser 'day' ou 'month' (recebido: {self.period})")
        if not 0 <= self.reserve < 1:
            raise ValueError("quota.reserve deve estar em [0, 1)")
        return self


class QuotaStateConfig(BaseModel):
    """Onde o consumo de cota é persistido e de quanto em quanto tempo sincronizar."""

    file: str = "./quota.json"
    sync_interval_s: float = 300.0  # 0 desliga a sincronização automática


class ProviderConfig(BaseModel):
    """Configuração de um provedor de voz."""

    type: str
    api_key_env: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    cooldown_s: float = 30.0
    weight: float = 1.0
    cost_per_char: float = 0.0
    quota: QuotaConfig | None = None
    max_chars: int | None = None  # sobrescreve o limite por chamada do provedor
    concurrency: int = 4  # pedaços sintetizados em paralelo (texto longo)


class ProviderRef(BaseModel):
    """Referência a um provedor dentro de uma rota (aceita string curta, ex.: 'edge')."""

    name: str
    weight: float = 1.0
    cost_per_char: float = 0.0
    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"name": v}
        return v


class RuleConfig(BaseModel):
    """Regra de roteamento: se `if` bate, a rota usa estes provedores/estratégia."""

    providers: list[ProviderRef] = Field(default_factory=list)
    strategy: str | None = None
    if_: dict[str, Any] = Field(default_factory=dict, alias="if")


class ModelRoute(BaseModel):
    """Rota: mapeia um nome de modelo (ex.: 'auto') para uma lista de provedores."""

    strategy: str = "first"
    providers: list[ProviderRef] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)


class VoiceAlias(BaseModel):
    """Voz lógica mapeada para uma voz equivalente em cada provedor.

    Aceita a forma curta `{edge: pt-BR-AntonioNeural, openai: onyx}` ou a
    longa `{description: ..., strict: true, voices: {...}}`.
    `strict: true` pula provedores sem mapeamento (em vez de usar a voz padrão).
    """

    voices: dict[str, str]
    description: str = ""
    strict: bool = False

    @model_validator(mode="before")
    @classmethod
    def _accept_short(cls, v: Any) -> Any:
        if isinstance(v, dict) and "voices" not in v:
            return {"voices": v}
        return v


class APIKeyConfig(BaseModel):
    """Chave de acesso de um cliente. Prefira `key_env` (variável de ambiente)."""

    name: str
    key: str | None = None
    key_env: str | None = None
    rpm: int | None = None  # requisições por minuto (None = default_rpm)

    @model_validator(mode="after")
    def _check(self) -> "APIKeyConfig":
        if not self.key and not self.key_env:
            raise ValueError(f"auth.keys[{self.name}]: defina 'key' ou 'key_env'")
        return self


class AuthConfig(BaseModel):
    enabled: bool = False
    keys: list[APIKeyConfig] = Field(default_factory=list)
    default_rpm: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "AuthConfig":
        if self.enabled and not self.keys:
            raise ValueError("auth.enabled=true exige ao menos uma chave em auth.keys")
        return self


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_s: int = 86400
    dir: str = "./cache"


class RouterConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelRoute]
    stt_models: dict[str, ModelRoute] = Field(default_factory=dict)
    voices: dict[str, VoiceAlias] = Field(default_factory=dict)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    quota_state: QuotaStateConfig = Field(default_factory=QuotaStateConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    default_model: str = "auto"
    default_stt_model: str = "auto"
    host: str = "127.0.0.1"
    port: int = 8000

    @model_validator(mode="after")
    def _validate_refs(self) -> "RouterConfig":
        def check_refs(refs: list[ProviderRef], where: str) -> None:
            for ref in refs:
                if ref.name not in self.providers:
                    raise ValueError(
                        f"{where}: provedor desconhecido '{ref.name}'. Disponíveis: {sorted(self.providers)}"
                    )

        for section, routes in (("models", self.models), ("stt_models", self.stt_models)):
            for name, route in routes.items():
                if route.strategy not in STRATEGIES:
                    raise ValueError(
                        f"{section}.{name}: estratégia '{route.strategy}' inválida. Use: {STRATEGIES}"
                    )
                check_refs(route.providers, f"{section}.{name}")
                for i, rule in enumerate(route.rules):
                    if rule.strategy and rule.strategy not in STRATEGIES:
                        raise ValueError(
                            f"{section}.{name}.rules[{i}]: estratégia '{rule.strategy}' inválida. Use: {STRATEGIES}"
                        )
                    check_refs(rule.providers, f"{section}.{name}.rules[{i}]")
        for alias, va in self.voices.items():
            for pname in va.voices:
                if pname not in self.providers:
                    raise ValueError(
                        f"voices.{alias}: provedor desconhecido '{pname}'. Disponíveis: {sorted(self.providers)}"
                    )
        return self


_ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def expand_env(text: str) -> str:
    """Expande ${VAR} e ${VAR:-padrão} (útil em Docker/compose)."""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), text)


def load_config(path: str | Path) -> RouterConfig:
    """Carrega e valida a configuração YAML."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    data = yaml.safe_load(expand_env(path.read_text(encoding="utf-8"))) or {}
    return RouterConfig(**data)
