"""Modelos de configuração (YAML) do VoxRouter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

STRATEGIES = ("first", "random", "round_robin", "weighted", "cheapest")


class ProviderConfig(BaseModel):
    """Configuração de um provedor de voz."""

    type: str
    api_key_env: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    cooldown_s: float = 30.0
    weight: float = 1.0
    cost_per_char: float = 0.0


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


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_s: int = 86400
    dir: str = "./cache"


class RouterConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelRoute]
    cache: CacheConfig = Field(default_factory=CacheConfig)
    default_model: str = "auto"
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

        for name, route in self.models.items():
            if route.strategy not in STRATEGIES:
                raise ValueError(f"models.{name}: estratégia '{route.strategy}' inválida. Use: {STRATEGIES}")
            check_refs(route.providers, f"models.{name}")
            for i, rule in enumerate(route.rules):
                if rule.strategy and rule.strategy not in STRATEGIES:
                    raise ValueError(
                        f"models.{name}.rules[{i}]: estratégia '{rule.strategy}' inválida. Use: {STRATEGIES}"
                    )
                check_refs(rule.providers, f"models.{name}.rules[{i}]")
        return self


def load_config(path: str | Path) -> RouterConfig:
    """Carrega e valida a configuração YAML."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RouterConfig(**data)
