"""Núcleo do VoxRouter: resolução de rotas, estratégias, failover e cache."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from .cache import AudioCache
from .config import ProviderRef, RouterConfig
from .providers import create_provider
from .providers.base import (
    BaseProvider,
    ProviderContext,
    ProviderError,
    SynthesisRequest,
    SynthesisResult,
)
from .state import CircuitBreaker, Stats


class UnknownModel(Exception):
    """Nome de modelo/rota não existe na configuração."""


class AllProvidersFailed(Exception):
    """Todos os provedores da rota falharam (ou estão em cooldown)."""

    def __init__(self, attempts: list[dict[str, str]]):
        self.attempts = attempts
        super().__init__("Todos os provedores falharam (ou estão em cooldown)")


@dataclass
class Candidate:
    provider: BaseProvider
    name: str
    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    cost_per_char: float = 0.0


class VoiceRouter:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.providers: dict[str, BaseProvider] = {
            name: create_provider(name, pcfg) for name, pcfg in config.providers.items()
        }
        self.breaker = CircuitBreaker()
        self.stats = Stats()
        self.cache = AudioCache(config.cache)
        self._rr: dict[str, int] = {}

    # ---------------------------------------------------------------- resolução

    def _direct(self, model: str) -> tuple[str, str] | None:
        """Sintaxe direta 'provedor/voz-ou-modelo' (ex.: edge/pt-BR-FranciscaNeural)."""
        if "/" in model:
            provider_name, rest = model.split("/", 1)
            if provider_name in self.providers and rest.strip():
                return provider_name, rest.strip()
        return None

    @staticmethod
    def _match_rule(req: SynthesisRequest, conditions: dict[str, Any]) -> bool:
        for key, value in conditions.items():
            if key == "voice" and req.voice != value:
                return False
            elif key == "voice_regex" and (req.voice is None or not re.search(value, req.voice)):
                return False
            elif key == "language" and req.language != value:
                return False
            elif key == "format" and req.response_format != value:
                return False
            elif key == "text_max_chars" and len(req.text) > value:
                return False
            elif key == "text_min_chars" and len(req.text) < value:
                return False
            elif key == "contains" and value not in req.text:
                return False
        return True

    def _candidate(self, ref: ProviderRef, direct_rest: str | None = None) -> Candidate:
        prov = self.providers[ref.name]
        model, voice = ref.model, ref.voice
        if direct_rest is not None:
            if prov.type == "elevenlabs" and not (
                direct_rest.startswith("eleven_") or direct_rest.startswith("turbo")
            ):
                voice = direct_rest  # 'elevenlabs/<id-da-voz>'
            elif prov.direct_as_voice:
                voice = direct_rest  # 'edge/<voz>', 'google/<voz>', ...
            else:
                model = direct_rest  # 'openai/<modelo>', 'omnivoice/<modelo>'
        pcfg = self.config.providers[ref.name]
        return Candidate(
            provider=prov,
            name=ref.name,
            model=model,
            voice=voice,
            options={**pcfg.options, **ref.options},
            weight=ref.weight if ref.weight != 1.0 else pcfg.weight,
            cost_per_char=ref.cost_per_char if ref.cost_per_char != 0 else pcfg.cost_per_char,
        )

    def _resolve(self, req: SynthesisRequest) -> tuple[list[Candidate], str]:
        model = req.model or self.config.default_model
        direct = self._direct(model)
        if direct:
            name, rest = direct
            return [self._candidate(ProviderRef(name=name), direct_rest=rest)], "first"
        route = self.config.models.get(model)
        if route is None:
            raise UnknownModel(model)
        strategy = route.strategy
        refs = route.providers
        for rule in route.rules:
            if self._match_rule(req, rule.if_):
                strategy = rule.strategy or strategy
                refs = rule.providers
                break
        return [self._candidate(r) for r in refs], strategy

    def _order(self, cands: list[Candidate], strategy: str, route_key: str) -> list[Candidate]:
        if strategy == "random":
            return random.sample(cands, len(cands))
        if strategy == "round_robin":
            idx = self._rr.get(route_key, 0)
            self._rr[route_key] = idx + 1
            if cands:
                return cands[idx % len(cands):] + cands[: idx % len(cands)]
        if strategy == "weighted":
            pool: list[Candidate] = []
            for c in cands:
                pool.extend([c] * max(1, int(round(c.weight * 10))))
            seen: list[Candidate] = []
            while pool:
                c = random.choice(pool)
                seen.append(c)
                pool = [x for x in pool if x is not c]
            return seen
        if strategy == "cheapest":
            return sorted(cands, key=lambda c: c.cost_per_char)
        return list(cands)  # first

    # ------------------------------------------------------------------ síntese

    def cache_key(self, req: SynthesisRequest, cand: Candidate) -> str:
        return AudioCache.key(
            {
                "provider": cand.name,
                "model": cand.model,
                "voice": cand.voice,
                "text": req.text,
                "format": req.response_format,
                "speed": req.speed,
                "language": req.language,
                "extra": req.extra,
                "options": cand.options,
            }
        )

    async def synthesize(self, req: SynthesisRequest) -> SynthesisResult:
        self.stats.requests += 1
        cands, strategy = self._resolve(req)
        ordered = self._order(cands, strategy, req.model or self.config.default_model)
        attempts: list[dict[str, str]] = []
        for cand in ordered:
            if not self.breaker.available(cand.name):
                continue
            ckey = self.cache_key(req, cand)
            if self.cache.enabled:
                hit = self.cache.get(ckey)
                if hit is not None:
                    audio, fmt = hit
                    self.stats.cache_hits += 1
                    return SynthesisResult(
                        audio=audio,
                        format=fmt,
                        provider=cand.name,
                        model=cand.model or "",
                        voice=cand.voice or "",
                        cached=True,
                    )
            ctx = ProviderContext(model=cand.model, voice=cand.voice, options=cand.options)
            try:
                res = await cand.provider.synthesize(req, ctx)
            except ProviderError as exc:
                self.breaker.record_failure(cand.name, cand.provider.cfg.cooldown_s)
                self.stats.record_fail(cand.name, str(exc))
                attempts.append({"provider": cand.name, "error": str(exc)})
                continue
            except Exception as exc:  # erro inesperado também vira failover
                self.breaker.record_failure(cand.name, cand.provider.cfg.cooldown_s)
                self.stats.record_fail(cand.name, f"{type(exc).__name__}: {exc}")
                attempts.append({"provider": cand.name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            self.breaker.record_success(cand.name)
            self.stats.record_ok(cand.name, res.latency_ms)
            if self.cache.enabled:
                self.cache.put(ckey, res.audio, res.format)
            return res
        if not attempts:
            attempts.append({"error": "todos os provedores estão em cooldown"})
        raise AllProvidersFailed(attempts)

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()

    def health(self) -> dict[str, Any]:
        providers = []
        for name, p in self.providers.items():
            d = p.describe()
            d.update(self.breaker.snapshot(name))
            providers.append(d)
        return {"status": "ok", "providers": providers}
