"""Testes de cota (quota-aware) e integração com o roteador."""

from __future__ import annotations

import asyncio
import datetime

from voxrouter.config import QuotaConfig, RouterConfig
from voxrouter.providers.base import SynthesisRequest
from voxrouter.quota import QuotaManager
from voxrouter.router import VoiceRouter


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- QuotaManager


def test_quota_manager_basico(tmp_path):
    qm = QuotaManager(tmp_path / "quota.json")
    q = QuotaConfig(limit=100, period="month")
    assert qm.status("edge", q) == "ok"
    assert qm.remaining("edge", q) == 100
    qm.consume("edge", q, 40)
    assert qm.used("edge", q) == 40
    qm.consume("edge", q, 60)
    assert qm.status("edge", q) == "exhausted"
    assert qm.remaining("edge", q) == 0


def test_quota_reserve_low(tmp_path):
    qm = QuotaManager(tmp_path / "q.json")
    q = QuotaConfig(limit=100, period="month", reserve=0.1)
    qm.consume("e", q, 90)
    assert qm.status("e", q) == "low"
    qm.consume("e", q, 11)
    assert qm.status("e", q) == "exhausted"


def test_quota_persistencia(tmp_path):
    f = tmp_path / "q.json"
    qm = QuotaManager(f)
    q = QuotaConfig(limit=1000, period="month")
    qm.consume("edge", q, 123)
    qm2 = QuotaManager(f)  # novo manager lê o estado do disco
    assert qm2.used("edge", q) == 123


def test_quota_reset(tmp_path):
    qm = QuotaManager(tmp_path / "q.json")
    q = QuotaConfig(limit=100, period="month")
    qm.consume("a", q, 50)
    qm.consume("b", q, 10)
    qm.reset("a")
    assert qm.used("a", q) == 0
    assert qm.used("b", q) == 10
    qm.reset()
    assert qm.used("b", q) == 0


def test_quota_period_key():
    assert QuotaManager.period_key("month", datetime.datetime(2026, 11, 13)) == "2026-11"
    assert QuotaManager.period_key("day", datetime.datetime(2026, 11, 13)) == "2026-11-13"


def test_quota_periodos_independentes():
    qm = QuotaManager("/tmp/nao-usado.json")
    q = QuotaConfig(limit=100, period="month")
    jan = QuotaManager.period_key("month", datetime.datetime(2026, 1, 5))
    fev = QuotaManager.period_key("month", datetime.datetime(2026, 2, 5))
    assert jan != fev


def test_quota_config_invalida():
    from pydantic import ValidationError

    import pytest

    with pytest.raises(ValidationError):
        QuotaConfig(limit=10, period="semana")
    with pytest.raises(ValidationError):
        QuotaConfig(limit=10, reserve=1.5)


# ------------------------------------------------------------------ integração


def _cfg(tmp_path, cache=False, strategy="first", providers=None, quotas=None):
    cfg = {
        "providers": {
            "m1": {"type": "mock"},
            "m2": {"type": "mock"},
        },
        "models": {"auto": {"strategy": strategy, "providers": providers or ["m1", "m2"]}},
        "cache": {"enabled": cache, "dir": str(tmp_path / "cache")},
        "quota_state": {"file": str(tmp_path / "quota.json")},
    }
    for name, q in (quotas or {}).items():
        cfg["providers"][name]["quota"] = q
    return cfg


def test_quota_pula_exausto(tmp_path):
    cfg = _cfg(tmp_path, quotas={"m1": {"limit": 0, "period": "month"}})
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto")))
    assert res.provider == "m2"  # m1 sem cota → pulado na ordem


def test_quota_consome_apenas_no_sucesso(tmp_path):
    cfg = _cfg(tmp_path, providers=["m1", "m2"], quotas={"m1": {"limit": 100, "period": "month"}})
    r = VoiceRouter(RouterConfig(**cfg))
    q = r.providers["m1"].cfg.quota
    run(r.synthesize(SynthesisRequest(text="x" * 30, model="auto")))
    assert r.quota.used("m1", q) == 30
    run(r.synthesize(SynthesisRequest(text="y" * 80, model="auto")))
    assert r.quota.used("m1", q) == 110  # estourou a cota
    res3 = run(r.synthesize(SynthesisRequest(text="z" * 10, model="auto")))
    assert res3.provider == "m2"  # m1 exausto → m2 assume


def test_quota_cache_hit_nao_consome(tmp_path):
    cfg = _cfg(tmp_path, cache=True, quotas={"m1": {"limit": 100, "period": "month"}})
    r = VoiceRouter(RouterConfig(**cfg))
    q = r.providers["m1"].cfg.quota
    text = "mesmo texto"
    res1 = run(r.synthesize(SynthesisRequest(text=text, model="auto")))
    assert res1.cached is False
    res2 = run(r.synthesize(SynthesisRequest(text=text, model="auto")))
    assert res2.cached is True
    assert r.quota.used("m1", q) == len(text)  # cache não consome


def test_quota_aware_ordena_por_custo(tmp_path):
    cfg = {
        "providers": {
            "caro": {"type": "mock", "cost_per_char": 0.9},
            "exausto": {"type": "mock", "cost_per_char": 0.0, "quota": {"limit": 0, "period": "month"}},
            "medio": {"type": "mock", "cost_per_char": 0.5},
        },
        "models": {"auto": {"strategy": "quota_aware", "providers": ["caro", "exausto", "medio"]}},
        "cache": {"enabled": False},
        "quota_state": {"file": str(tmp_path / "q.json")},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto")))
    assert res.provider == "medio"  # mais barato ENTRE os que têm cota


def test_quota_todos_exaustos_degrada_graciosamente(tmp_path):
    cfg = _cfg(
        tmp_path,
        quotas={"m1": {"limit": 0, "period": "month"}, "m2": {"limit": 0, "period": "month"}},
    )
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto")))
    assert res.provider in ("m1", "m2")  # sem alternativa → mantém ordem normal


def test_quota_low_vai_pro_fim(tmp_path):
    cfg = _cfg(tmp_path, quotas={"m1": {"limit": 100, "period": "month", "reserve": 0.9}})
    r = VoiceRouter(RouterConfig(**cfg))
    q = r.providers["m1"].cfg.quota
    r.quota.consume("m1", q, 20)  # remaining 80 <= 90 (reserva) → "low"
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto")))
    assert res.provider == "m2"  # m1 está "low" → vai pro fim da fila


def test_health_tem_quota(tmp_path):
    cfg = _cfg(tmp_path, quotas={"m1": {"limit": 500, "period": "month"}})
    r = VoiceRouter(RouterConfig(**cfg))
    h = r.health()
    by_name = {p["name"]: p for p in h["providers"]}
    assert by_name["m1"]["quota"]["status"] == "ok"
    assert by_name["m1"]["quota"]["limit"] == 500
    assert by_name["m2"]["quota"] is None
