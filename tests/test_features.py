"""Testes dos recursos do v0.2: aliases, divisão de texto, streaming,
transcrição, autenticação, sync de cotas e formatos de requisição dos
provedores novos (transport mock — sem rede)."""

from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from voxrouter.api import create_app
from voxrouter.audio import concat_audio, split_text, strip_id3
from voxrouter.config import ProviderConfig, RouterConfig
from voxrouter.providers.base import (
    ProviderContext,
    ProviderError,
    SynthesisRequest,
    TranscriptionRequest,
)
from voxrouter.router import VoiceRouter


def run(coro):
    return asyncio.run(coro)


BASE_CFG = {
    "providers": {"m1": {"type": "mock"}, "m2": {"type": "mock"}},
    "models": {"auto": {"providers": ["m1", "m2"]}},
    "cache": {"enabled": False},
}


# ---------------------------------------------------------------- helpers áudio


def test_split_text_por_frases():
    assert split_text("Um. Dois! Três?", 6) == ["Um.", "Dois!", "Três?"]


def test_split_text_corte_seco():
    assert split_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_split_text_curto_nao_divide():
    assert split_text("curto", 100) == ["curto"]


def test_strip_id3():
    import struct

    data = b"ID3\x04\x00\x00\x00\x00\x00\x00abc"  # header de 10 bytes, tamanho 0
    assert strip_id3(data) == b"abc"
    assert strip_id3(b"plain") == b"plain"


def test_concat_audio_mp3_sem_id3_duplicada():
    chunk = b"ID3\x04\x00\x00\x00\x00\x00\x00mp3data"
    out = concat_audio([chunk, chunk, chunk], "mp3")
    assert out.count(b"ID3") == 1  # só o cabeçalho do primeiro pedaço


def test_concat_audio_wav(tmp_path):
    def make(seconds: float) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * int(24000 * seconds))
        return buf.getvalue()

    out = concat_audio([make(0.1), make(0.2)], "wav")
    with wave.open(io.BytesIO(out), "rb") as w:
        assert w.getnframes() == int(24000 * 0.3)


def test_concat_audio_formato_sem_suporte():
    with pytest.raises(ValueError):
        concat_audio([b"a", b"b"], "opus")


# ---------------------------------------------------------------- aliases de voz


def test_alias_mapeia_por_provedor():
    cfg = {
        "providers": {
            "m1": {"type": "mock", "options": {"voice": "v1"}},
            "m2": {"type": "mock", "options": {"voice": "v2"}},
        },
        "models": {"auto": {"providers": ["m1", "m2"]}},
        "voices": {"narrador": {"voices": {"m1": "vozA", "m2": "vozB"}}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto", voice="narrador")))
    assert res.provider == "m1"
    assert res.voice == "vozA"


def test_alias_acompanha_o_failover():
    cfg = {
        "providers": {
            "m1": {"type": "mock", "options": {"fail": True}},
            "m2": {"type": "mock"},
        },
        "models": {"auto": {"providers": ["m1", "m2"]}},
        "voices": {"narrador": {"voices": {"m1": "vozA", "m2": "vozB"}}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto", voice="narrador")))
    assert res.provider == "m2"
    assert res.voice == "vozB"  # a voz equivalente do provedor que assumiu


def test_alias_strict_pula_sem_mapeamento():
    cfg = {
        "providers": {
            "m1": {"type": "mock"},
            "m2": {"type": "mock"},
        },
        "models": {"auto": {"providers": ["m2", "m1"]}},
        "voices": {"narrador": {"strict": True, "voices": {"m1": "vozA"}}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto", voice="narrador")))
    assert res.provider == "m1"  # m2 não tem voz no alias → pulado


def test_alias_inexistente_nao_quebra():
    r = VoiceRouter(RouterConfig(**BASE_CFG))
    res = run(r.synthesize(SynthesisRequest(text="oi", model="auto", voice="nao-existe")))
    assert res.provider == "m1"  # voz desconhecida vira a padrão do provedor


# ------------------------------------------------------------- textos longos


def test_chunking_texto_longo(tmp_path):
    cfg = {
        "providers": {"m1": {"type": "mock", "max_chars": 100}},
        "models": {"auto": {"providers": ["m1"]}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    text = "Frase número um. " * 8  # 128 caracteres, limite 100
    res = run(r.synthesize(SynthesisRequest(text=text, model="auto")))
    assert res.chunks == 2
    assert res.audio[:4] == b"RIFF"
    with wave.open(io.BytesIO(res.audio), "rb") as w:
        assert w.getnframes() > 0


# ------------------------------------------------------------------ streaming


def _collect(coro):
    async def go():
        parts = []
        async for c in coro:
            parts.append(c)
        return b"".join(parts)

    return asyncio.run(go())


def _stream_collect(r, req):
    """Roda stream + coleta no MESMO event loop (geradores não cruzam loops)."""

    async def go():
        meta, body = await r.stream(req)
        parts = []
        async for c in body:
            parts.append(c)
        return meta, b"".join(parts)

    return asyncio.run(go())


def test_stream_basico():
    r = VoiceRouter(RouterConfig(**BASE_CFG))
    meta, audio = _stream_collect(r, SynthesisRequest(text="oi", model="auto"))
    assert meta.provider == "m1"
    assert meta.format == "wav"
    assert audio[:4] == b"RIFF"


def test_stream_failover_antes_do_primeiro_byte():
    cfg = {
        "providers": {
            "bad": {"type": "mock", "options": {"fail": True}},
            "ok": {"type": "mock"},
        },
        "models": {"auto": {"providers": ["bad", "ok"]}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    meta, body = run(r.stream(SynthesisRequest(text="oi", model="auto")))
    assert meta.provider == "ok"  # trocou ANTES de mandar qualquer byte


def test_stream_interrompido_no_meio():
    cfg = {
        "providers": {"m1": {"type": "mock", "options": {"stream_fail_after": 1}}},
        "models": {"auto": {"providers": ["m1"]}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    meta, truncated = _stream_collect(r, SynthesisRequest(text="oi", model="auto"))
    assert meta.provider == "m1"
    assert len(truncated) > 0 and len(truncated) < 10000  # corpo parcial entregue
    assert r.stats.snapshot()["providers"]["m1"]["fail"] == 1  # falha registrada


def test_stream_cache_hit(tmp_path):
    cfg = dict(BASE_CFG)
    cfg["cache"] = {"enabled": True, "ttl_s": 60, "dir": str(tmp_path / "cache")}
    r = VoiceRouter(RouterConfig(**cfg))
    req = SynthesisRequest(text="mesmo", model="auto")
    _, a1 = _stream_collect(r, req)
    meta2, a2 = _stream_collect(r, req)
    assert meta2.cached is True
    assert a2 == a1


def test_stream_texto_longo_via_buffer(tmp_path):
    cfg = {
        "providers": {"m1": {"type": "mock", "max_chars": 100}},
        "models": {"auto": {"providers": ["m1"]}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    text = "Frase número um. " * 8
    meta, audio = _stream_collect(r, SynthesisRequest(text=text, model="auto"))
    assert meta.chunks == 2  # WAV não streama por partes: monta e entrega
    assert audio[:4] == b"RIFF"


# ---------------------------------------------------------------- transcrição


def test_transcribe_rota():
    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.transcribe(TranscriptionRequest(audio=b"audio123", model="auto")))
    assert res.provider == "m1"
    assert "mock" in res.text


def test_transcribe_failover():
    cfg = {
        "providers": {
            "bad": {"type": "mock", "options": {"fail": True}},
            "ok": {"type": "mock"},
        },
        "models": {"auto": {"providers": ["bad", "ok"]}},
        "stt_models": {"auto": {"providers": ["bad", "ok"]}},
        "cache": {"enabled": False},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.transcribe(TranscriptionRequest(audio=b"x", model="auto")))
    assert res.provider == "ok"


def test_transcribe_cache(tmp_path):
    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    cfg["cache"] = {"enabled": True, "ttl_s": 60, "dir": str(tmp_path / "cache")}
    r = VoiceRouter(RouterConfig(**cfg))
    req = TranscriptionRequest(audio=b"mesmo audio", model="auto")
    assert run(r.transcribe(req)).cached is False
    assert run(r.transcribe(req)).cached is True


def test_transcribe_direto():
    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    r = VoiceRouter(RouterConfig(**cfg))
    res = run(r.transcribe(TranscriptionRequest(audio=b"x", model="m1/meu-modelo")))
    assert res.provider == "m1"
    assert res.model == "meu-modelo"


def test_transcribe_modelo_desconhecido():
    from voxrouter.router import UnknownModel

    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    r = VoiceRouter(RouterConfig(**cfg))
    with pytest.raises(UnknownModel):
        run(r.transcribe(TranscriptionRequest(audio=b"x", model="nao-existe")))


# ---------------------------------------------------------------- sync de cotas


def test_quota_sync_sobrescreve_uso(tmp_path):
    cfg = {
        "providers": {
            "m1": {
                "type": "mock",
                "options": {"remote_used": 300, "remote_limit": 1000},
                "quota": {"sync": True, "period": "month"},
            }
        },
        "models": {"auto": {"providers": ["m1"]}},
        "cache": {"enabled": False},
        "quota_state": {"file": str(tmp_path / "quota.json")},
    }
    r = VoiceRouter(RouterConfig(**cfg))
    out = run(r.sync_quotas())
    assert out["m1"]["ok"] is True
    assert out["m1"]["used"] == 300
    q = r.providers["m1"].cfg.quota
    assert q.limit == 1000  # limite aprendido do provedor
    assert r.quota.used("m1", q) == 300


# ------------------------------------------------------------------ autenticação


def _authed_client(cfg: dict):
    return TestClient(create_app(RouterConfig(**cfg)))


def test_auth_obrigatoria():
    cfg = dict(BASE_CFG)
    cfg["auth"] = {"enabled": True, "keys": [{"name": "cliente", "key": "segredo", "rpm": 10}]}
    c = _authed_client(cfg)
    assert c.get("/health").status_code == 200  # health aberto
    assert c.post("/v1/audio/speech", json={"model": "auto", "input": "oi"}).status_code == 401
    ok = c.post(
        "/v1/audio/speech",
        json={"model": "auto", "input": "oi"},
        headers={"Authorization": "Bearer segredo"},
    )
    assert ok.status_code == 200
    assert ok.headers["x-provider"] == "m1"


def test_rate_limit_429():
    cfg = dict(BASE_CFG)
    cfg["auth"] = {"enabled": True, "keys": [{"name": "cliente", "key": "segredo", "rpm": 2}]}
    c = _authed_client(cfg)
    h = {"Authorization": "Bearer segredo"}
    for _ in range(2):
        assert c.post("/v1/audio/speech", json={"model": "auto", "input": "oi"}, headers=h).status_code == 200
    terceiro = c.post("/v1/audio/speech", json={"model": "auto", "input": "oi"}, headers=h)
    assert terceiro.status_code == 429
    assert "retry-after" in terceiro.headers


# ------------------------------------------------------------------ API (HTTP)


def test_speech_stream_endpoint():
    c = TestClient(create_app(RouterConfig(**BASE_CFG)))
    r = c.post("/v1/audio/speech", json={"model": "auto", "input": "oi", "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")
    assert r.headers["x-provider"] == "m1"
    assert r.content[:4] == b"RIFF"


def test_transcriptions_endpoint():
    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    c = TestClient(create_app(RouterConfig(**cfg)))
    r = c.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"audio", "audio/wav")},
        data={"model": "auto", "response_format": "text"},
    )
    assert r.status_code == 200
    assert r.headers["x-provider"] == "m1"
    assert "mock" in r.text


def test_models_lista_stt():
    cfg = dict(BASE_CFG)
    cfg["stt_models"] = {"auto": {"providers": ["m1"]}}
    c = TestClient(create_app(RouterConfig(**cfg)))
    data = c.get("/v1/models").json()["data"]
    assert {m["type"] for m in data} == {"tts", "stt"}


def test_voices_lista_alias():
    cfg = dict(BASE_CFG)
    cfg["voices"] = {"narrador": {"description": "voz lógica", "voices": {"m1": "vozA"}}}
    c = TestClient(create_app(RouterConfig(**cfg)))
    data = c.get("/v1/voices", params={"provider": "alias"}).json()["data"]
    assert [v["voice"] for v in data] == ["narrador"]


def test_quota_sync_endpoint(tmp_path):
    cfg = {
        "providers": {"m1": {"type": "mock", "options": {"remote_used": 42}, "quota": {"sync": True}}},
        "models": {"auto": {"providers": ["m1"]}},
        "cache": {"enabled": False},
        "quota_state": {"file": str(tmp_path / "q.json")},
    }
    c = TestClient(create_app(RouterConfig(**cfg)))
    r = c.post("/quota/sync")
    assert r.status_code == 200
    assert r.json()["synced"]["m1"]["ok"] is True


# ------------------------------------------- formatos de requisição (sem rede)


def _provider(cls, options=None, monkeypatch=None):
    from voxrouter.providers import REGISTRY

    p = cls("teste", ProviderConfig(type=cls.type, api_key_env="TEST_KEY", options=options or {}))
    if monkeypatch:
        monkeypatch.setenv("TEST_KEY", "chave-teste")
    return p


def _capture(p, response):
    captured = {}
    content = response.get("content", b"\x00" * 8)
    if "json" in response:
        content = json.dumps(response["json"]).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, content=content)

    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x.test")
    return captured


def test_cartesia_formato_requisicao(monkeypatch):
    from voxrouter.providers.cartesia import CartesiaProvider

    p = _provider(CartesiaProvider, {"voice": "id-voz-1"}, monkeypatch)
    cap = _capture(p, {"content": b"\x00" * 16})
    run(p.synthesize(SynthesisRequest(text="Olá", response_format="mp3"), ProviderContext()))
    req = cap["request"]
    assert str(req.url) == "https://api.cartesia.ai/tts/bytes"
    assert req.headers["authorization"] == "Bearer chave-teste"
    assert req.headers["cartesia-version"] == "2026-08-14"
    body = json.loads(req.content)
    assert body["model_id"] == "sonic-3.6"
    assert body["transcript"] == "Olá"
    assert body["voice"] == "id-voz-1"
    assert body["output_format"]["container"] == "mp3"


def test_deepgram_tts_formato_requisicao(monkeypatch):
    from voxrouter.providers.deepgram import DeepgramProvider

    p = _provider(DeepgramProvider, None, monkeypatch)
    cap = _capture(p, {"content": b"\x00" * 16})
    run(p.synthesize(SynthesisRequest(text="Olá"), ProviderContext()))
    req = cap["request"]
    assert req.url.path == "/v1/speak"
    assert req.headers["authorization"] == "Token chave-teste"
    assert req.url.params["model"] == "aura-2-thalia-en"
    assert req.url.params["encoding"] == "mp3"
    assert json.loads(req.content) == {"text": "Olá"}


def test_deepgram_stt_resposta(monkeypatch):
    from voxrouter.providers.deepgram import DeepgramProvider

    p = _provider(DeepgramProvider, None, monkeypatch)
    _capture(
        p,
        {"json": {"results": {"channels": [{"alternatives": [{"transcript": "Oi, tudo bem"}]}]}}},
    )
    res = run(p.transcribe(TranscriptionRequest(audio=b"xyz", language="pt-BR"), ProviderContext()))
    assert res.text == "Oi, tudo bem"
    assert res.language == "pt"


def test_fish_formato_requisicao(monkeypatch):
    from voxrouter.providers.fish import FishAudioProvider

    p = _provider(FishAudioProvider, None, monkeypatch)
    cap = _capture(p, {"content": b"\x00" * 16})
    run(p.synthesize(SynthesisRequest(text="Olá", voice="voz-modelo"), ProviderContext()))
    req = cap["request"]
    assert str(req.url) == "https://api.fish.audio/v1/tts"
    assert req.headers["authorization"] == "Bearer chave-teste"
    assert req.headers["model"] == "s2.1-pro"
    body = json.loads(req.content)
    assert body["text"] == "Olá"
    assert body["reference_id"] == "voz-modelo"
    assert body["format"] == "mp3"


def test_openai_stt_formato_requisicao(monkeypatch):
    from voxrouter.providers.openai import OpenAIProvider

    p = _provider(OpenAIProvider, None, monkeypatch)
    cap = _capture(p, {"json": {"text": "oi"}})
    run(p.transcribe(TranscriptionRequest(audio=b"xyz"), ProviderContext()))
    req = cap["request"]
    assert str(req.url) == "https://api.openai.com/v1/audio/transcriptions"
    assert b'name="model"' in req.content and b"whisper-1" in req.content
    assert b'filename="audio"' in req.content


def test_elevenlabs_usage_remote(monkeypatch):
    from voxrouter.providers.elevenlabs import ElevenLabsProvider

    p = _provider(ElevenLabsProvider, None, monkeypatch)
    _capture(
        p,
        {"json": {"character_count": 4321, "character_limit": 10000, "next_character_count_reset_unix": 1738356858}},
    )
    usage = run(p.remote_usage())
    assert usage["used"] == 4321
    assert usage["limit"] == 10000


def test_provedor_sem_capacidade_na_rota():
    cfg = {
        "providers": {"w": {"type": "whisper_local"}},
        "models": {"auto": {"providers": ["w"]}},  # whisper_local é só STT
        "cache": {"enabled": False},
    }
    with pytest.raises(ValueError, match="não suporta TTS"):
        VoiceRouter(RouterConfig(**cfg))
