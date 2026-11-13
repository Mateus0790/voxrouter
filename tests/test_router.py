"""Testes do núcleo de roteamento (sem rede)."""

from __future__ import annotations

import asyncio

import pytest

from voxrouter.config import RouterConfig
from voxrouter.providers.base import SynthesisRequest
from voxrouter.router import AllProvidersFailed, UnknownModel, VoiceRouter


def make_router(cfg: dict) -> VoiceRouter:
    return VoiceRouter(RouterConfig(**cfg))


def run(coro):
    return asyncio.run(coro)


MOCK_CFG = {
    "providers": {
        "m1": {"type": "mock"},
        "m2": {"type": "mock", "options": {"fail": True}},
        "m3": {"type": "mock"},
    },
    "models": {
        "ok": {"providers": ["m1"]},
        "failover": {"providers": ["m2", "m1"]},
        "rr": {"strategy": "round_robin", "providers": ["m1", "m3"]},
        "cheap": {
            "strategy": "cheapest",
            "providers": [
                {"name": "m1", "cost_per_char": 0.5},
                {"name": "m3", "cost_per_char": 0.1},
            ],
        },
        "ruled": {
            "providers": ["m3"],
            "rules": [{"if": {"voice_regex": "^pt-"}, "providers": ["m1"]}],
        },
    },
    "cache": {"enabled": False},
}


def test_sintese_basica():
    r = make_router(MOCK_CFG)
    res = run(r.synthesize(SynthesisRequest(text="olá mundo", model="ok")))
    assert res.provider == "m1"
    assert res.audio[:4] == b"RIFF"
    assert res.format == "wav"


def test_failover():
    r = make_router(MOCK_CFG)
    res = run(r.synthesize(SynthesisRequest(text="oi", model="failover")))
    assert res.provider == "m1"  # m2 falhou, m1 assumiu


def test_failover_total():
    cfg = {
        "providers": {"bad": {"type": "mock", "options": {"fail": True}}},
        "models": {"x": {"providers": ["bad"]}},
        "cache": {"enabled": False},
    }
    r = make_router(cfg)
    with pytest.raises(AllProvidersFailed) as ei:
        run(r.synthesize(SynthesisRequest(text="oi", model="x")))
    assert ei.value.attempts[0]["provider"] == "bad"


def test_circuit_breaker_cooldown():
    cfg = {
        "providers": {"bad": {"type": "mock", "options": {"fail": True}, "cooldown_s": 5}},
        "models": {"x": {"providers": ["bad"]}},
        "cache": {"enabled": False},
    }
    r = make_router(cfg)
    with pytest.raises(AllProvidersFailed):
        run(r.synthesize(SynthesisRequest(text="oi", model="x")))
    with pytest.raises(AllProvidersFailed) as ei:
        run(r.synthesize(SynthesisRequest(text="oi", model="x")))
    assert ei.value.attempts[0].get("provider") is None  # nem tentou: em cooldown
    assert "cooldown" in ei.value.attempts[0]["error"]


def test_round_robin():
    r = make_router(MOCK_CFG)
    a = run(r.synthesize(SynthesisRequest(text="oi", model="rr"))).provider
    b = run(r.synthesize(SynthesisRequest(text="oi", model="rr"))).provider
    assert {a, b} == {"m1", "m3"}


def test_cheapest():
    r = make_router(MOCK_CFG)
    res = run(r.synthesize(SynthesisRequest(text="oi", model="cheap")))
    assert res.provider == "m3"  # custo 0.1 < 0.5


def test_rules():
    r = make_router(MOCK_CFG)
    res = run(r.synthesize(SynthesisRequest(text="oi", model="ruled", voice="pt-BR-X")))
    assert res.provider == "m1"
    res2 = run(r.synthesize(SynthesisRequest(text="oi", model="ruled", voice="en-X")))
    assert res2.provider == "m3"


def test_sintaxe_direta():
    r = make_router(MOCK_CFG)
    res = run(r.synthesize(SynthesisRequest(text="oi", model="m1/tone2")))
    assert res.provider == "m1"
    assert res.model == "tone2"


def test_modelo_desconhecido():
    r = make_router(MOCK_CFG)
    with pytest.raises(UnknownModel):
        run(r.synthesize(SynthesisRequest(text="oi", model="nao-existe")))


def test_provedor_desconhecido_na_config():
    with pytest.raises(ValueError):
        make_router(
            {
                "providers": {"m1": {"type": "mock"}},
                "models": {"x": {"providers": ["ghost"]}},
                "cache": {"enabled": False},
            }
        )


def test_cache_hit(tmp_path):
    cfg = {
        "providers": {"m1": {"type": "mock"}},
        "models": {"ok": {"providers": ["m1"]}},
        "cache": {"enabled": True, "ttl_s": 3600, "dir": str(tmp_path / "cache")},
    }
    r = make_router(cfg)

    def req():
        return SynthesisRequest(text="texto cacheado", model="ok")

    res1 = run(r.synthesize(req()))
    assert res1.cached is False
    res2 = run(r.synthesize(req()))
    assert res2.cached is True
    assert res2.audio == res1.audio
    assert r.stats.snapshot()["cache_hits"] == 1
    r.cache.clear()
