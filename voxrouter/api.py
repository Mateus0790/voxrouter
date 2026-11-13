"""API HTTP do VoxRouter (compatível com OpenAI /v1/audio/speech)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from .config import RouterConfig, load_config
from .providers.base import SynthesisRequest
from .router import AllProvidersFailed, UnknownModel, VoiceRouter

MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}

KNOWN_FIELDS = {"model", "input", "text", "voice", "response_format", "speed", "language"}


def create_app(config: RouterConfig | str | Path) -> FastAPI:
    cfg = config if isinstance(config, RouterConfig) else load_config(config)
    router = VoiceRouter(cfg)
    started = time.monotonic()

    app = FastAPI(
        title="VoxRouter",
        version="0.1.0",
        description="Roteador de voz unificado — API compatível com OpenAI /v1/audio/speech",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.router = router
    app.state.started = started

    @app.get("/", include_in_schema=False)
    async def ui() -> HTMLResponse:
        return HTMLResponse(UI_HTML)

    @app.post("/v1/audio/speech")
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
            result = await router.synthesize(req)
        except UnknownModel as exc:
            raise HTTPException(
                404,
                detail=(
                    f"modelo desconhecido: '{exc.args[0]}'. Modelos: {sorted(cfg.models)} "
                    f"— ou use a sintaxe direta 'provedor/voz' (ex.: edge/pt-BR-AntonioNeural)"
                ),
            )
        except AllProvidersFailed as exc:
            raise HTTPException(503, detail={"message": str(exc), "attempts": exc.attempts})
        return Response(
            content=result.audio,
            media_type=MIME.get(result.format, "audio/mpeg"),
            headers={
                "X-Provider": result.provider,
                "X-Model": result.model or "",
                "X-Voice": result.voice or "",
                "X-Latency-Ms": str(result.latency_ms),
                "X-Cache": "HIT" if result.cached else "MISS",
            },
        )

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        data = [
            {
                "id": name,
                "object": "model",
                "owned_by": "voxrouter",
                "providers": [r.name for r in route.providers],
            }
            for name, route in cfg.models.items()
        ]
        return {
            "object": "list",
            "data": data,
            "direct_syntax": [f"{p}/..." for p in cfg.providers],
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        h = router.health()
        h["uptime_s"] = round(time.monotonic() - started, 1)
        return h

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return router.stats.snapshot()

    @app.get("/quota")
    async def quota() -> dict[str, Any]:
        return router.quota.snapshot(cfg.providers)

    @app.post("/quota/reset")
    async def quota_reset(provider: str | None = None) -> dict[str, Any]:
        router.quota.reset(provider)
        return {"reset": provider or "*"}

    @app.get("/v1/voices")
    async def voices(
        provider: str | None = None,
        language: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        names = [provider] if provider else list(router.providers)
        data: list[dict[str, Any]] = []
        for name in names:
            p = router.providers.get(name)
            if p is None:
                raise HTTPException(
                    400,
                    detail=f"provedor desconhecido: '{name}'. Disponíveis: {sorted(router.providers)}",
                )
            try:
                data.extend(await p.list_voices())
            except Exception:
                pass  # catálogo é melhor esforço; nunca derruba o endpoint
        if language:
            lang = language.lower()
            data = [v for v in data if (v.get("locale") or "").lower().startswith(lang)]
        if search:
            s = search.lower()
            data = [
                v
                for v in data
                if s in v.get("voice", "").lower() or s in (v.get("label") or "").lower()
            ]
        return {"object": "list", "data": data[: max(1, min(limit, 1000))]}

    @app.post("/cache/clear", include_in_schema=False)
    async def clear_cache() -> dict[str, Any]:
        return {"removed": router.cache.clear()}

    return app


UI_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxRouter — Console</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e8eaf0; --mut:#9aa3b2; --acc:#7c5cff; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:ui-sans-serif,system-ui,'Segoe UI',sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:760px; margin:0 auto; padding:40px 20px 80px; }
  h1 { font-size:22px; margin:0 0 4px; }
  h1 span { color:var(--acc); }
  .sub { color:var(--mut); margin:0 0 28px; font-size:14px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .full { grid-column:1 / -1; }
  label { display:block; font-size:12px; color:var(--mut); margin:0 0 6px; text-transform:uppercase; letter-spacing:.06em; }
  textarea, select, input[type=text] { width:100%; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:14px; font-family:inherit; }
  textarea { min-height:96px; resize:vertical; }
  button { grid-column:1 / -1; background:var(--acc); color:#fff; border:0; border-radius:10px; padding:12px; font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  #status { margin-top:14px; font-size:13px; color:var(--mut); min-height:18px; }
  #status.err { color:#ff6b6b; }
  audio { width:100%; margin-top:16px; }
  details { margin-top:20px; color:var(--mut); font-size:13px; }
  code { background:var(--card); padding:2px 6px; border-radius:6px; }
</style>
</head>
<body>
<main>
  <h1>Vox<span>Router</span></h1>
  <p class="sub">Console de teste — roteia sua síntese de voz para o melhor provedor disponível.</p>
  <div class="grid">
    <div><label>Modelo (rota)</label><select id="model"></select></div>
    <div><label>Voz (opcional — sobrescreve a rota)</label><input type="text" id="voice" placeholder="ex.: pt-BR-AntonioNeural" list="voices"></div>
    <datalist id="voices">
      <option value="pt-BR-AntonioNeural"></option>
      <option value="pt-BR-FranciscaNeural"></option>
      <option value="pt-BR-ThalitaNeural"></option>
      <option value="pt-BR-BrendaNeural"></option>
      <option value="alloy"></option>
      <option value="nova"></option>
      <option value="af_heart"></option>
    </datalist>
    <div><label>Formato</label><select id="fmt"><option>mp3</option><option>wav</option><option>opus</option><option>aac</option><option>flac</option><option>pcm</option></select></div>
    <div><label>Velocidade</label><select id="speed"><option value="1">1.0&times;</option><option value="1.25">1.25&times;</option><option value="0.75">0.75&times;</option></select></div>
    <div class="full"><label>Texto</label><textarea id="text">Olá! Este é um teste do VoxRouter, gerado por inteligência artificial.</textarea></div>
    <button id="go">&#9654; Gerar voz</button>
  </div>
  <div id="status"></div>
  <audio id="player" controls style="display:none"></audio>
  <details><summary>Rotas &amp; sintaxe direta</summary><pre id="hint"></pre></details>
  <details><summary>Cotas (quota-aware)</summary><pre id="quota">carregando...</pre></details>
</main>
<script>
const $ = (id) => document.getElementById(id);
const status = (msg, err) => { $('status').textContent = msg; $('status').className = err ? 'err' : ''; };
async function loadModels() {
  try {
    const r = await fetch('/v1/models');
    const j = await r.json();
    $('model').innerHTML = j.data.map(m => '<option value="' + m.id + '">' + m.id + ' (' + m.providers.join(', ') + ')</option>').join('');
    $('hint').textContent = 'Sintaxe direta: ' + j.direct_syntax.join('  ') + '\\n\\n' + JSON.stringify(j.data, null, 2);
  } catch (e) { status('Servidor offline? Rode: python -m voxrouter --config config.example.yaml', true); }
}
async function go() {
  const btn = $('go'); btn.disabled = true; $('player').style.display = 'none';
  status('Gerando...');
  try {
    const body = { model: $('model').value, input: $('text').value, response_format: $('fmt').value, speed: parseFloat($('speed').value) };
    const v = $('voice').value.trim(); if (v) body.voice = v;
    const t0 = performance.now();
    const r = await fetch('/v1/audio/speech', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); const d = j.detail; throw new Error(typeof d === 'string' ? d : (d && d.message || JSON.stringify(d))); }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    $('player').src = url; $('player').style.display = 'block'; $('player').play();
    status('OK: ' + r.headers.get('X-Provider') + ' | ' + (r.headers.get('X-Voice') || r.headers.get('X-Model')) + ' | ' + r.headers.get('X-Latency-Ms') + 'ms | cache ' + r.headers.get('X-Cache') + ' | ' + Math.round(performance.now() - t0) + 'ms total');
  } catch (e) { status('Erro: ' + e.message, true); }
  finally { btn.disabled = false; }
}
async function loadVoices() {
  try {
    const r = await fetch('/v1/voices?limit=500');
    const j = await r.json();
    $('voices').innerHTML = j.data.map(v => '<option value="' + v.voice + '">').join('');
  } catch (e) {}
}
async function loadQuota() {
  try { const j = await (await fetch('/quota')).json(); $('quota').textContent = JSON.stringify(j, null, 2); } catch (e) {}
}
$('go').onclick = go;
loadModels();
loadVoices();
loadQuota();
</script>
</body>
</html>
"""
