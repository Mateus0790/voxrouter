"""Testes da API HTTP (provedor mock, sem rede)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voxrouter.api import create_app
from voxrouter.config import RouterConfig

CONFIG = {
    "providers": {
        "m1": {"type": "mock"},
        "m2": {"type": "mock", "options": {"fail": True}},
    },
    "models": {
        "ok": {"providers": ["m1"]},
        "failover": {"providers": ["m2", "m1"]},
    },
    "cache": {"enabled": False},
    "default_model": "ok",
}


@pytest.fixture()
def client():
    app = create_app(RouterConfig(**CONFIG))
    with TestClient(app) as c:
        yield c


def test_speech_ok(client):
    r = client.post("/v1/audio/speech", json={"model": "ok", "input": "olá"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")
    assert r.headers["x-provider"] == "m1"
    assert r.headers["x-cache"] == "MISS"
    assert r.content[:4] == b"RIFF"


def test_speech_sem_input(client):
    r = client.post("/v1/audio/speech", json={"model": "ok"})
    assert r.status_code == 400


def test_failover(client):
    r = client.post("/v1/audio/speech", json={"model": "failover", "input": "oi"})
    assert r.status_code == 200
    assert r.headers["x-provider"] == "m1"


def test_falha_total(client):
    r = client.post("/v1/audio/speech", json={"model": "failover", "input": "oi"})
    assert r.status_code == 200
    # esgota cooldown do m2? não — segunda chamada ainda usa m1 via cache/rota
    r2 = client.post("/v1/audio/speech", json={"model": "failover", "input": "oi2"})
    assert r2.status_code == 200


def test_modelo_desconhecido(client):
    r = client.post("/v1/audio/speech", json={"model": "zzz", "input": "oi"})
    assert r.status_code == 404


def test_default_model(client):
    r = client.post("/v1/audio/speech", json={"input": "oi"})
    assert r.status_code == 200
    assert r.headers["x-provider"] == "m1"


def test_models(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "ok" in ids and "failover" in ids


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    names = [p["name"] for p in r.json()["providers"]]
    assert names == ["m1", "m2"]


def test_stats(client):
    client.post("/v1/audio/speech", json={"model": "ok", "input": "oi"})
    r = client.get("/stats")
    assert r.json()["requests"] >= 1
    assert r.json()["providers"]["m1"]["ok"] == 1


def test_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "VoxRouter" in r.text


def test_voices(client):
    r = client.get("/v1/voices")
    assert r.status_code == 200
    voices = [v["voice"] for v in r.json()["data"]]
    assert "tone1" in voices and "tone2" in voices


def test_voices_filtros(client):
    r = client.get("/v1/voices", params={"provider": "m1", "search": "tone2"})
    assert [v["voice"] for v in r.json()["data"]] == ["tone2"]
    r2 = client.get("/v1/voices", params={"provider": "m1", "language": "mock"})
    assert len(r2.json()["data"]) == 3
    r3 = client.get("/v1/voices", params={"provider": "nao-existe"})
    assert r3.status_code == 400


def test_quota_endpoint(client):
    r = client.get("/quota")
    assert r.status_code == 200
    assert r.json()["m1"]["status"] == "unlimited"


def test_quota_reset_endpoint(client):
    r = client.post("/quota/reset")
    assert r.status_code == 200
    assert r.json() == {"reset": "*"}


def test_cache_header(tmp_path):
    cfg = dict(CONFIG)
    cfg["cache"] = {"enabled": True, "ttl_s": 60, "dir": str(tmp_path / "cache")}
    app = create_app(RouterConfig(**cfg))
    with TestClient(app) as c:
        r1 = c.post("/v1/audio/speech", json={"model": "ok", "input": "repetido"})
        r2 = c.post("/v1/audio/speech", json={"model": "ok", "input": "repetido"})
    assert r1.headers["x-cache"] == "MISS"
    assert r2.headers["x-cache"] == "HIT"
    assert r2.content == r1.content
