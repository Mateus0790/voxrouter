"""Núcleo do VoxRouter: rotas, estratégias, failover, cotas, cache, aliases,
divisão de textos longos, streaming e transcrição."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, AsyncGenerator, AsyncIterator

from .audio import CONCAT_FORMATS, STREAM_CONCAT_FORMATS, concat_audio, split_text, strip_id3
from .cache import AudioCache
from .config import ModelRoute, ProviderRef, RouterConfig
from .providers import create_provider
from .providers.base import (
    BaseProvider,
    ProviderContext,
    ProviderError,
    SynthesisRequest,
    SynthesisResult,
    TranscriptionRequest,
    TranscriptionResult,
)
from .quota import QuotaManager
from .state import CircuitBreaker, Stats

log = logging.getLogger("voxrouter")


class UnknownModel(Exception):
    """Nome de modelo/rota não existe na configuração."""


class BadRequest(Exception):
    """Pedido inválido para o recurso solicitado (vira HTTP 400)."""


class AllProvidersFailed(Exception):
    """Todos os provedores da rota falharam (ou estão em cooldown)."""

    def __init__(self, attempts: list[dict[str, str]]):
        self.attempts = attempts
        super().__init__("Todos os provedores falharam (ou estão em cooldown)")


_SKIP = object()  # alias estrito sem voz para o provedor


@dataclass
class Candidate:
    provider: BaseProvider
    name: str
    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    cost_per_char: float = 0.0


@dataclass
class StreamMeta:
    provider: str
    format: str
    model: str = ""
    voice: str = ""
    ttfb_ms: int = 0
    chunks: int = 1
    cached: bool = False


class VoiceRouter:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.providers: dict[str, BaseProvider] = {
            name: create_provider(name, pcfg) for name, pcfg in config.providers.items()
        }
        self._check_capabilities()
        self.breaker = CircuitBreaker()
        self.stats = Stats()
        self.cache = AudioCache(config.cache)
        self.quota = QuotaManager(config.quota_state.file)
        self._rr: dict[str, int] = {}

    def _check_capabilities(self) -> None:
        for kind, section, routes in (
            ("tts", "models", self.config.models),
            ("stt", "stt_models", self.config.stt_models),
        ):
            for rname, route in routes.items():
                refs = list(route.providers) + [r for rule in route.rules for r in rule.providers]
                for ref in refs:
                    p = self.providers[ref.name]
                    if kind not in p.capabilities:
                        raise ValueError(
                            f"{section}.{rname}: provedor '{ref.name}' (tipo {p.type}) não suporta "
                            f"{kind.upper()} — capacidades: {sorted(p.capabilities)}"
                        )

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

    def _candidate(self, ref: ProviderRef, direct_rest: str | None = None, kind: str = "tts") -> Candidate:
        prov = self.providers[ref.name]
        model, voice = ref.model, ref.voice
        if direct_rest is not None:
            if kind == "stt":
                model = direct_rest  # 'openai/whisper-1', 'deepgram/nova-3'
            elif prov.type == "elevenlabs" and not (
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

    def _resolve_route(
        self, model: str, kind: str, routes: dict[str, ModelRoute], req: SynthesisRequest | None
    ) -> tuple[list[Candidate], str]:
        direct = self._direct(model)
        if direct:
            name, rest = direct
            if kind not in self.providers[name].capabilities:
                raise BadRequest(f"provedor '{name}' não suporta {kind.upper()}")
            return [self._candidate(ProviderRef(name=name), direct_rest=rest, kind=kind)], "first"
        route = routes.get(model)
        if route is None:
            raise UnknownModel(model)
        strategy, refs = route.strategy, route.providers
        if req is not None:
            for rule in route.rules:
                if self._match_rule(req, rule.if_):
                    strategy = rule.strategy or strategy
                    refs = rule.providers
                    break
        return [self._candidate(r, kind=kind) for r in refs], strategy

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

    _QUOTA_RANK = {"unlimited": 0, "ok": 0, "low": 1, "exhausted": 2}

    def _quota_class(self, cand: Candidate) -> str:
        q = cand.provider.cfg.quota
        if q is None:
            return "unlimited"
        return self.quota.status(cand.name, q)

    def _quota_order(self, cands: list[Candidate], strategy: str) -> list[Candidate]:
        """Quota-aware: exaustos vão pro fim; 'low' atrás dos 'ok'.

        Se TODOS estão exaustos, mantém a ordem normal (degradação graciosa em
        vez de 503) — comportamento inspirado no OmniRoute.
        """
        if not cands:
            return cands
        base = list(cands)
        if strategy == "quota_aware":
            base = sorted(base, key=lambda c: c.cost_per_char)
        if any(self._quota_class(c) != "exhausted" for c in base):
            base = sorted(base, key=lambda c: self._QUOTA_RANK[self._quota_class(c)])
        return base

    def _plan(self, req: SynthesisRequest) -> list[Candidate]:
        model = req.model or self.config.default_model
        cands, strategy = self._resolve_route(model, "tts", self.config.models, req)
        return self._quota_order(self._order(cands, strategy, model), strategy)

    # ----------------------------------------------------------- aliases de voz

    def _voice_for(self, req: SynthesisRequest, cand: Candidate) -> Any:
        """Voz efetiva para este candidato (rota/sintaxe direta > alias > pedido)."""
        if cand.voice:
            return cand.voice
        if req.voice and req.voice in self.config.voices:
            alias = self.config.voices[req.voice]
            mapped = alias.voices.get(cand.name)
            if mapped is None and alias.strict:
                return _SKIP
            return mapped  # None = voz padrão do provedor
        return req.voice

    def _prepare(self, req: SynthesisRequest, cand: Candidate) -> tuple[SynthesisRequest, ProviderContext] | None:
        voice = self._voice_for(req, cand)
        if voice is _SKIP:
            return None
        creq = replace(req, voice=voice)
        return creq, ProviderContext(model=cand.model, voice=voice, options=cand.options)

    # ------------------------------------------------------------------ síntese

    def cache_key(self, req: SynthesisRequest, cand: Candidate) -> str:
        return AudioCache.key(
            {
                "provider": cand.name,
                "model": cand.model,
                "voice": req.voice,
                "text": req.text,
                "format": req.response_format,
                "speed": req.speed,
                "language": req.language,
                "extra": req.extra,
                "options": cand.options,
            }
        )

    def _fail(self, cand: Candidate, exc: Exception, attempts: list[dict[str, str]]) -> None:
        msg = str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: {exc}"
        self.breaker.record_failure(cand.name, cand.provider.cfg.cooldown_s)
        self.stats.record_fail(cand.name, msg)
        attempts.append({"provider": cand.name, "error": msg})

    def _success(self, cand: Candidate, req: SynthesisRequest, latency_ms: int) -> None:
        self.breaker.record_success(cand.name)
        self.stats.record_ok(cand.name, latency_ms)
        q = cand.provider.cfg.quota
        if q is not None:
            self.quota.consume(cand.name, q, len(req.text))

    async def _synth_chunked(
        self, cand: Candidate, req: SynthesisRequest, ctx: ProviderContext
    ) -> SynthesisResult:
        """Sintetiza; se o texto passar do limite do provedor, divide, gera em
        paralelo e concatena. Falha de qualquer pedaço = falha do candidato."""
        prov = cand.provider
        pieces = split_text(req.text, prov.max_chars)
        if len(pieces) == 1:
            return await prov.synthesize(req, ctx)
        fmt = prov.output_format(req)
        if fmt not in CONCAT_FORMATS:
            raise ProviderError(
                cand.name,
                f"texto de {len(req.text)} caracteres excede o limite de {prov.max_chars} e o formato "
                f"'{fmt}' não pode ser concatenado (use mp3, wav, pcm ou aac)",
            )
        t0 = time.monotonic()
        sem = asyncio.Semaphore(max(1, prov.cfg.concurrency))

        async def one(text: str) -> SynthesisResult:
            async with sem:
                return await prov.synthesize(replace(req, text=text), ctx)

        tasks = [asyncio.ensure_future(one(t)) for t in pieces]
        try:
            parts = await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            raise
        try:
            audio = concat_audio([p.audio for p in parts], parts[0].format)
        except ValueError as exc:
            raise ProviderError(cand.name, str(exc)) from exc
        return SynthesisResult(
            audio=audio,
            format=parts[0].format,
            provider=cand.name,
            model=parts[0].model,
            voice=parts[0].voice,
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks=len(pieces),
        )

    async def synthesize(self, req: SynthesisRequest) -> SynthesisResult:
        self.stats.requests += 1
        attempts: list[dict[str, str]] = []
        for cand in self._plan(req):
            if not self.breaker.available(cand.name):
                continue
            prepared = self._prepare(req, cand)
            if prepared is None:
                attempts.append({"provider": cand.name, "error": f"alias '{req.voice}' sem voz para este provedor"})
                continue
            creq, ctx = prepared
            ckey = self.cache_key(creq, cand)
            if self.cache.enabled:
                hit = self.cache.get(ckey)
                if hit is not None:
                    audio, fmt = hit
                    self.stats.cache_hits += 1
                    return SynthesisResult(
                        audio=audio, format=fmt, provider=cand.name,
                        model=cand.model or "", voice=creq.voice or "", cached=True,
                    )
            try:
                res = await self._synth_chunked(cand, creq, ctx)
            except Exception as exc:  # ProviderError ou inesperado: failover
                self._fail(cand, exc, attempts)
                continue
            self._success(cand, creq, res.latency_ms)
            if self.cache.enabled:
                self.cache.put(ckey, res.audio, res.format)
            return res
        if not attempts:
            attempts.append({"error": "todos os provedores estão em cooldown"})
        raise AllProvidersFailed(attempts)

    # ---------------------------------------------------------------- streaming

    async def _stream_pieces(
        self, cand: Candidate, req: SynthesisRequest, ctx: ProviderContext, pieces: list[str]
    ) -> AsyncGenerator[bytes, None]:
        fmt = cand.provider.output_format(req)
        for i, text in enumerate(pieces):
            first = True
            async for chunk in cand.provider.stream(replace(req, text=text), ctx):
                if first and i > 0 and fmt == "mp3":
                    chunk = strip_id3(chunk)
                first = False
                if chunk:
                    yield chunk

    async def stream(self, req: SynthesisRequest) -> tuple[StreamMeta, AsyncIterator[bytes]]:
        """Streaming com failover ANTES do primeiro byte (depois disso não há volta)."""
        self.stats.requests += 1
        attempts: list[dict[str, str]] = []
        for cand in self._plan(req):
            if not self.breaker.available(cand.name):
                continue
            prepared = self._prepare(req, cand)
            if prepared is None:
                attempts.append({"provider": cand.name, "error": f"alias '{req.voice}' sem voz para este provedor"})
                continue
            creq, ctx = prepared
            ckey = self.cache_key(creq, cand)
            if self.cache.enabled:
                hit = self.cache.get(ckey)
                if hit is not None:
                    audio, fmt = hit
                    self.stats.cache_hits += 1
                    meta = StreamMeta(cand.name, fmt, cand.model or "", creq.voice or "", 0, 1, True)
                    return meta, _iter_bytes(audio)

            pieces = split_text(creq.text, cand.provider.max_chars)
            fmt = cand.provider.output_format(creq)
            t0 = time.monotonic()

            if len(pieces) > 1 and fmt not in STREAM_CONCAT_FORMATS:
                # ex.: WAV longo — cabeçalho único exige montar tudo antes (buffer)
                try:
                    res = await self._synth_chunked(cand, creq, ctx)
                except Exception as exc:
                    self._fail(cand, exc, attempts)
                    continue
                self._success(cand, creq, res.latency_ms)
                if self.cache.enabled:
                    self.cache.put(ckey, res.audio, res.format)
                meta = StreamMeta(cand.name, res.format, res.model, res.voice, res.latency_ms, res.chunks)
                return meta, _iter_bytes(res.audio)

            gen = self._stream_pieces(cand, creq, ctx, pieces)
            try:
                first = await gen.__anext__()
            except StopAsyncIteration:
                self._fail(cand, ProviderError(cand.name, "stream vazio"), attempts)
                continue
            except Exception as exc:
                await gen.aclose()
                self._fail(cand, exc, attempts)
                continue
            ttfb = int((time.monotonic() - t0) * 1000)
            self.breaker.record_success(cand.name)
            meta = StreamMeta(cand.name, fmt, cand.model or "", creq.voice or "", ttfb, len(pieces))
            return meta, self._stream_body(cand, creq, ckey, fmt, first, gen, ttfb)

        if not attempts:
            attempts.append({"error": "todos os provedores estão em cooldown"})
        raise AllProvidersFailed(attempts)

    async def _stream_body(
        self, cand: Candidate, req: SynthesisRequest, ckey: str, fmt: str,
        first: bytes, gen: AsyncGenerator[bytes, None], ttfb: int,
    ) -> AsyncIterator[bytes]:
        buf = bytearray(first)
        completed = False
        try:
            yield first
            async for chunk in gen:
                buf.extend(chunk)
                yield chunk
            completed = True
        except Exception as exc:  # falha no meio do stream: não há como trocar de provedor
            self.breaker.record_failure(cand.name, cand.provider.cfg.cooldown_s)
            self.stats.record_fail(cand.name, f"stream interrompido: {exc}")
            log.warning("stream de %s interrompido: %s", cand.name, exc)
        finally:
            if completed:
                self._success(cand, req, ttfb)
                if self.cache.enabled:
                    self.cache.put(ckey, bytes(buf), fmt)

    # -------------------------------------------------------------- transcrição

    async def transcribe(self, req: TranscriptionRequest) -> TranscriptionResult:
        self.stats.requests += 1
        model = req.model or self.config.default_stt_model
        if not self.config.stt_models and not self._direct(model):
            raise UnknownModel(model)
        cands, strategy = self._resolve_route(model, "stt", self.config.stt_models, None)
        audio_hash = hashlib.sha256(req.audio).hexdigest()
        attempts: list[dict[str, str]] = []
        for cand in self._order(cands, strategy, "stt:" + model):
            if not self.breaker.available(cand.name):
                continue
            ckey = AudioCache.key(
                {"stt": True, "provider": cand.name, "model": cand.model, "audio": audio_hash,
                 "language": req.language, "prompt": req.prompt, "options": cand.options}
            )
            if self.cache.enabled:
                hit = self.cache.get(ckey)
                if hit is not None:
                    self.stats.cache_hits += 1
                    data = json.loads(hit[0].decode("utf-8"))
                    return TranscriptionResult(**{**data, "cached": True})
            ctx = ProviderContext(model=cand.model, options=cand.options)
            try:
                res = await cand.provider.transcribe(req, ctx)
            except Exception as exc:
                self._fail(cand, exc, attempts)
                continue
            self.breaker.record_success(cand.name)
            self.stats.record_ok(cand.name, res.latency_ms)
            if self.cache.enabled:
                payload = {k: v for k, v in res.__dict__.items() if k != "cached"}
                self.cache.put(ckey, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "json")
            return res
        if not attempts:
            attempts.append({"error": "todos os provedores estão em cooldown"})
        raise AllProvidersFailed(attempts)

    # ------------------------------------------------------- sync de cotas

    async def sync_quotas(self) -> dict[str, Any]:
        """Puxa o uso real dos provedores com `quota.sync: true`."""
        out: dict[str, Any] = {}
        for name, p in self.providers.items():
            q = p.cfg.quota
            if q is None or not q.sync:
                continue
            try:
                usage = await p.remote_usage()
            except Exception as exc:
                out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                continue
            if not usage:
                out[name] = {"ok": False, "error": "provedor não informa uso (sem suporte ou sem chave)"}
                continue
            if usage.get("limit") is not None:
                q.limit = int(usage["limit"])
            self.quota.set_used(name, q, int(usage["used"]))
            out[name] = {"ok": True, **usage}
        return out

    def has_sync(self) -> bool:
        return any(p.cfg.quota is not None and p.cfg.quota.sync for p in self.providers.values())

    # ------------------------------------------------------------------- misc

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()

    def health(self) -> dict[str, Any]:
        providers = []
        for name, p in self.providers.items():
            d = p.describe()
            d.update(self.breaker.snapshot(name))
            d["quota"] = None if p.cfg.quota is None else self.quota.describe(name, p.cfg.quota)
            providers.append(d)
        return {"status": "ok", "providers": providers}


async def _iter_bytes(data: bytes, size: int = 32768) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i:i + size]
