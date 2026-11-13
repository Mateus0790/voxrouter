"""CLI do VoxRouter."""

from __future__ import annotations

import argparse

import uvicorn

from .api import create_app
from .config import load_config


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="voxrouter",
        description="Roteador de voz unificado (API compatível com OpenAI /v1/audio/speech)",
    )
    parser.add_argument("--config", default="config.yaml", help="caminho do arquivo YAML de configuração")
    parser.add_argument("--host", default=None, help="host (padrão: config)")
    parser.add_argument("--port", type=int, default=None, help="porta (padrão: config)")
    parser.add_argument("--reload", action="store_true", help="auto-reload em mudanças (dev)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    app = create_app(cfg)
    uvicorn.run(app, host=args.host or cfg.host, port=args.port or cfg.port, reload=args.reload)
