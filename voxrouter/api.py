"""API HTTP do VoxRouter (compatível com OpenAI /v1/audio/speech e /v1/audio/transcriptions)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from . import __version__
from .auth import Authenticator, AuthError
from .config import RouterConfig, load_config
from .providers.base import SynthesisRequest, TranscriptionRequest
from .router import AllProvidersFailed, BadRequest, UnknownModel, VoiceRouter

log = logging.getLogger("voxrouter.api")

MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}
KNOWN_FIELDS = {"model", "input", "text", "voice", "response_format", "speed", "language", "stream"}
EXPOSED = ["X-Provider", "X-Model", "X-Voice", "X-Latency-Ms", "X-TTFB-Ms", "X-Cache", "X-Chunks"]
MAX_UPLOAD = 25 * 1024 * 1024  # mesmo limite da OpenAI


def _h(value: Any) -> str:
    """Valor seguro para cabeçalho HTTP (latin-1)."""
    return str(value or "").encode("latin-1", "replace").decode("latin-1")


def create_app(config: RouterConfig | str | Path) -> FastAPI:
    cfg = config if isinstance(config, RouterConfig) else load_config(config)
    router = VoiceRouter(cfg)
    auth = Authenticator(cfg.auth)
    started = time.monotonic()

    async def sync_loop() -> None:
        while True:
            try:
                await router.sync_quotas()
            except Exception:
                log.exception("falha na sincronização de cotas")
            await asyncio.sleep(cfg.quota_state.sync_interval_s)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        task = None
        if router.has_sync() and cfg.quota_state.sync_interval_s > 0:
            task = asyncio.create_task(sync_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await router.aclose()

    app = FastAPI(
        title="VoxRouter",
        version=__version__,
        description="Roteador de voz unificado — TTS e STT com API compatível com OpenAI",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=EXPOSED
    )
    app.state.router = router

    async def require_auth(request: Request) -> str:
        try:
            return auth.check(request.headers.get("authorization"), request.headers.get("x-api-key"))
        except AuthError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {"Retry-After": str(exc.retry_after)}
            raise HTTPException(exc.status, detail=str(exc), headers=headers)

    protected = [Depends(require_auth)]

    def route_error(exc: Exception, kind: str = "tts") -> HTTPException:
        if isinstance(exc, UnknownModel):
            routes = sorted(cfg.models if kind == "tts" else cfg.stt_models)
            return HTTPException(
                404,
                detail=f"modelo desconhecido: '{exc.args[0]}'. Rotas: {routes} — ou use 'provedor/voz-ou-modelo'",
            )
        if isinstance(exc, BadRequest):
            return HTTPException(400, detail=str(exc))
        if isinstance(exc, AllProvidersFailed):
            return HTTPException(503, detail={"message": str(exc), "attempts": exc.attempts})
        raise exc

    # ------------------------------------------------------------------ TTS

    @app.post("/v1/audio/speech", dependencies=protected)
    async def speech(payload: dict[str, Any]) -> Response:
        text = payload.get("input") or payload.get("text")
        if not text or not str(text).strip():
            raise HTTPException(400, detail="campo 'input' (texto) é obrigatório")
        try:
            speed = float(payload.get("speed") or 1.0)
        except (TypeError, ValueError):
            raise HTTPException(400, detail="campo 'speed' deve ser numérico")
        req = SynthesisRequest(
            text=str(text),
            model=payload.get("model"),
            voice=payload.get("voice"),
            language=payload.get("language"),
            response_format=payload.get("response_format") or "mp3",
            speed=speed,
            extra={k: v for k, v in payload.items() if k not in KNOWN_FIELDS},
        )
        try:
            if payload.get("stream"):
                meta, body = await router.stream(req)
                return StreamingResponse(
                    body,
                    media_type=MIME.get(meta.format, "audio/mpeg"),
                    headers={
                        "X-Provider": _h(meta.provider),
                        "X-Model": _h(meta.model),
                        "X-Voice": _h(meta.voice),
                        "X-TTFB-Ms": str(meta.ttfb_ms),
                        "X-Chunks": str(meta.chunks),
                        "X-Cache": "HIT" if meta.cached else "MISS",
                    },
                )
            result = await router.synthesize(req)
        except (UnknownModel, BadRequest, AllProvidersFailed) as exc:
            raise route_error(exc)
        return Response(
            content=result.audio,
            media_type=MIME.get(result.format, "audio/mpeg"),
            headers={
                "X-Provider": _h(result.provider),
                "X-Model": _h(result.model),
                "X-Voice": _h(result.voice),
                "X-Latency-Ms": str(result.latency_ms),
                "X-Chunks": str(result.chunks),
                "X-Cache": "HIT" if result.cached else "MISS",
            },
        )

    # ------------------------------------------------------------------ STT

    @app.post("/v1/audio/transcriptions", dependencies=protected)
    async def transcriptions(
        file: UploadFile = File(...),
        model: str | None = Form(None),
        language: str | None = Form(None),
        prompt: str | None = Form(None),
        response_format: str = Form("json"),
    ) -> Response:
        audio = await file.read()
        if not audio:
            raise HTTPException(400, detail="arquivo de áudio vazio")
        if len(audio) > MAX_UPLOAD:
            raise HTTPException(413, detail=f"arquivo maior que {MAX_UPLOAD // (1024 * 1024)} MB")
        req = TranscriptionRequest(
            audio=audio,
            filename=file.filename or "audio",
            content_type=file.content_type or "application/octet-stream",
            model=model,
            language=language,
            prompt=prompt,
        )
        try:
            res = await router.transcribe(req)
        except (UnknownModel, BadRequest, AllProvidersFailed) as exc:
            raise route_error(exc, kind="stt")
        headers = {
            "X-Provider": _h(res.provider),
            "X-Model": _h(res.model),
            "X-Latency-Ms": str(res.latency_ms),
            "X-Cache": "HIT" if res.cached else "MISS",
        }
        if response_format == "text":
            return PlainTextResponse(res.text, headers=headers)
        if response_format == "verbose_json":
            return JSONResponse(
                {"text": res.text, "language": res.language, "duration": res.duration,
                 "segments": res.segments, "provider": res.provider, "model": res.model},
                headers=headers,
            )
        return JSONResponse({"text": res.text}, headers=headers)

    # ------------------------------------------------------------- catálogo

    @app.get("/v1/models", dependencies=protected)
    async def models() -> dict[str, Any]:
        data = [
            {"id": name, "object": "model", "owned_by": "voxrouter", "type": kind,
             "strategy": route.strategy, "providers": [r.name for r in route.providers]}
            for kind, routes in (("tts", cfg.models), ("stt", cfg.stt_models))
            for name, route in routes.items()
        ]
        return {
            "object": "list",
            "data": data,
            "direct_syntax": [f"{p}/..." for p in cfg.providers],
            "voice_aliases": sorted(cfg.voices),
        }

    @app.get("/v1/voices", dependencies=protected)
    async def voices(
        provider: str | None = None,
        language: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        if provider in (None, "alias"):
            data.extend(
                {"voice": name, "provider": "alias", "label": a.description or name,
                 "locale": None, "gender": None, "maps": a.voices, "strict": a.strict}
                for name, a in cfg.voices.items()
            )
        if provider != "alias":
            names = [provider] if provider else list(router.providers)
            for name in names:
                p = router.providers.get(name)
                if p is None:
                    raise HTTPException(
                        400, detail=f"provedor desconhecido: '{name}'. Disponíveis: {sorted(router.providers)} ou 'alias'"
                    )
                try:
                    data.extend(await p.list_voices())
                except Exception:
                    pass  # catálogo é melhor esforço; nunca derruba o endpoint
        if language:
            lang = language.lower()
            data = [v for v in data if (v.get("locale") or "").lower().startswith(lang) or v.get("provider") == "alias"]
        if search:
            s = search.lower()
            data = [v for v in data if s in (v.get("voice") or "").lower() or s in (v.get("label") or "").lower()]
        return {"object": "list", "data": data[: max(1, min(limit, 1000))]}

    # --------------------------------------------------------------- operação

    @app.get("/health")
    async def health() -> dict[str, Any]:
        h = router.health()
        h["version"] = __version__
        h["auth"] = auth.enabled
        h["uptime_s"] = round(time.monotonic() - started, 1)
        return h

    @app.get("/stats", dependencies=protected)
    async def stats() -> dict[str, Any]:
        return router.stats.snapshot()

    @app.get("/quota", dependencies=protected)
    async def quota() -> dict[str, Any]:
        return router.quota.snapshot(cfg.providers)

    @app.post("/quota/reset", dependencies=protected)
    async def quota_reset(provider: str | None = None) -> dict[str, Any]:
        router.quota.reset(provider)
        return {"reset": provider or "*"}

    @app.post("/quota/sync", dependencies=protected)
    async def quota_sync() -> dict[str, Any]:
        return {"synced": await router.sync_quotas()}

    @app.post("/cache/clear", dependencies=protected, include_in_schema=False)
    async def clear_cache() -> dict[str, Any]:
        return {"removed": router.cache.clear()}

    @app.get("/", include_in_schema=False)
    async def ui() -> HTMLResponse:
        return HTMLResponse(UI_HTML)

    return app


UI_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxRouter — Console</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e8eaf0; --mut:#9aa3b2; --acc:#7c5cff; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:ui-sans-serif,system-ui,'Segoe UI',sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:780px; margin:0 auto; padding:36px 20px 80px; }
  h1 { font-size:22px; margin:0 0 4px; } h1 span { color:var(--acc); }
  h2 { font-size:15px; margin:30px 0 12px; color:var(--mut); text-transform:uppercase; letter-spacing:.08em; }
  .sub { color:var(--mut); margin:0 0 20px; font-size:14px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .full { grid-column:1 / -1; }
  label { display:block; font-size:12px; color:var(--mut); margin:0 0 6px; text-transform:uppercase; letter-spacing:.06em; }
  textarea, select, input[type=text], input[type=password], input[type=file] { width:100%; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:14px; font-family:inherit; }
  textarea { min-height:96px; resize:vertical; }
  .check { display:flex; align-items:center; gap:8px; font-size:14px; color:var(--fg); text-transform:none; letter-spacing:0; margin-top:26px; }
  button { grid-column:1 / -1; background:var(--acc); color:#fff; border:0; border-radius:10px; padding:12px; font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .status { margin-top:12px; font-size:13px; color:var(--mut); min-height:18px; }
  .status.err { color:#ff6b6b; }
  audio { width:100%; margin-top:14px; }
  pre.out { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; white-space:pre-wrap; font-size:13px; }
  details { margin-top:16px; color:var(--mut); font-size:13px; }
</style>
</head>
<body>
<main>
  <h1>Vox<span>Router</span></h1>
  <p class="sub">Console de teste — síntese (TTS) e transcrição (STT) roteadas para o melhor provedor disponível.</p>
  <div class="grid"><div class="full"><label>Chave de API (se auth estiver ativa)</label><input type="password" id="apikey" placeholder="vazio = sem autenticação"></div></div>

  <h2>Síntese de voz</h2>
  <div class="grid">
    <div><label>Modelo (rota)</label><select id="model"></select></div>
    <div><label>Voz ou alias (opcional)</label><input type="text" id="voice" placeholder="ex.: narrador-pt" list="voices"></div>
    <datalist id="voices"></datalist>
    <div><label>Formato</label><select id="fmt"><option>mp3</option><option>wav</option><option>opus</option><option>aac</option><option>flac</option><option>pcm</option></select></div>
    <div><label class="check"><input type="checkbox" id="stream" checked> Streaming (primeiro som mais rápido)</label></div>
    <div class="full"><label>Texto</label><textarea id="text">Olá! Este é um teste do VoxRouter, gerado por inteligência artificial.</textarea></div>
    <button id="go">&#9654; Gerar voz</button>
  </div>
  <div class="status" id="status"></div>
  <audio id="player" controls style="display:none"></audio>

  <h2>Transcrição</h2>
  <div class="grid">
    <div><label>Modelo STT</label><select id="sttmodel"></select></div>
    <div><label>Idioma (opcional)</label><input type="text" id="lang" placeholder="pt"></div>
    <div class="full"><label>Arquivo de áudio</label><input type="file" id="file" accept="audio/*"></div>
    <button id="tr">&#127908; Transcrever</button>
  </div>
  <div class="status" id="trstatus"></div>
  <pre class="out" id="trout" style="display:none"></pre>

  <details><summary>Rotas, aliases &amp; sintaxe direta</summary><pre id="hint"></pre></details>
  <details><summary>Cotas</summary><pre id="quota">carregando...</pre></details>
</main>
<script>
const $ = (id) => document.getElementById(id);
const setStatus = (el, msg, err) => { $(el).textContent = msg; $(el).className = 'status' + (err ? ' err' : ''); };
$('apikey').value = localStorage.getItem('voxrouter-key') || '';
$('apikey').onchange = () => { localStorage.setItem('voxrouter-key', $('apikey').value); loadAll(); };
function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  const k = $('apikey').value.trim(); if (k) h['Authorization'] = 'Bearer ' + k;
  return h;
}
async function errText(r) {
  const j = await r.json().catch(() => ({})); const d = j.detail;
  return typeof d === 'string' ? d : (d && d.message ? d.message + ' — ' + JSON.stringify(d.attempts) : r.status + ' ' + r.statusText);
}
async function loadAll() {
  try {
    const r = await fetch('/v1/models', { headers: authHeaders() });
    if (!r.ok) throw new Error(await errText(r));
    const j = await r.json();
    const opt = (m) => '<option value="' + m.id + '">' + m.id + ' (' + m.providers.join(', ') + ')</option>';
    $('model').innerHTML = j.data.filter(m => m.type === 'tts').map(opt).join('');
    $('sttmodel').innerHTML = j.data.filter(m => m.type === 'stt').map(opt).join('') || '<option value="">(nenhuma rota stt_models)</option>';
    $('hint').textContent = 'Sintaxe direta: ' + j.direct_syntax.join('  ') + '\nAliases de voz: ' + (j.voice_aliases.join(', ') || '(nenhum)') + '\n\n' + JSON.stringify(j.data, null, 2);
    const v = await (await fetch('/v1/voices?limit=1000', { headers: authHeaders() })).json();
    $('voices').innerHTML = v.data.map(x => '<option value="' + x.voice + '">' + (x.provider === 'alias' ? 'alias' : x.provider) + '</option>').join('');
    const q = await (await fetch('/quota', { headers: authHeaders() })).json();
    $('quota').textContent = JSON.stringify(q, null, 2);
    setStatus('status', '');
  } catch (e) { setStatus('status', 'Erro ao carregar: ' + e.message, true); }
}
async function go() {
  const btn = $('go'); btn.disabled = true; $('player').style.display = 'none';
  setStatus('status', 'Gerando...');
  try {
    const body = { model: $('model').value, input: $('text').value, response_format: $('fmt').value, stream: $('stream').checked };
    const v = $('voice').value.trim(); if (v) body.voice = v;
    const t0 = performance.now();
    const r = await fetch('/v1/audio/speech', { method: 'POST', headers: authHeaders({'Content-Type': 'application/json'}), body: JSON.stringify(body) });
    if (!r.ok) throw new Error(await errText(r));
    const reader = r.body.getReader(); const parts = []; let first = null;
    while (true) { const { done, value } = await reader.read(); if (done) break; if (first === null) first = performance.now() - t0; parts.push(value); }
    const total = performance.now() - t0;
    const blob = new Blob(parts, { type: r.headers.get('content-type') || 'audio/mpeg' });
    $('player').src = URL.createObjectURL(blob); $('player').style.display = 'block'; $('player').play();
    const g = (h) => r.headers.get(h);
    setStatus('status', 'OK: ' + g('X-Provider') + ' · ' + (g('X-Voice') || g('X-Model')) + ' · 1º byte ' + Math.round(first) + 'ms · total ' + Math.round(total) + 'ms · pedaços ' + g('X-Chunks') + ' · cache ' + g('X-Cache'));
  } catch (e) { setStatus('status', 'Erro: ' + e.message, true); }
  finally { btn.disabled = false; }
}
async function transcribe() {
  const f = $('file').files[0]; if (!f) { setStatus('trstatus', 'Escolha um arquivo de áudio.', true); return; }
  const btn = $('tr'); btn.disabled = true; $('trout').style.display = 'none';
  setStatus('trstatus', 'Transcrevendo...');
  try {
    const fd = new FormData(); fd.append('file', f); fd.append('response_format', 'verbose_json');
    if ($('sttmodel').value) fd.append('model', $('sttmodel').value);
    if ($('lang').value.trim()) fd.append('language', $('lang').value.trim());
    const r = await fetch('/v1/audio/transcriptions', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) throw new Error(await errText(r));
    const j = await r.json();
    $('trout').textContent = j.text; $('trout').style.display = 'block';
    setStatus('trstatus', 'OK: ' + j.provider + ' · ' + j.model + ' · idioma ' + j.language + ' · ' + r.headers.get('X-Latency-Ms') + 'ms · cache ' + r.headers.get('X-Cache'));
  } catch (e) { setStatus('trstatus', 'Erro: ' + e.message, true); }
  finally { btn.disabled = false; }
}
$('go').onclick = go; $('tr').onclick = transcribe;
loadAll();
</script>
</body>
</html>
"""
