"""Cache em disco: chave = hash determinístico dos parâmetros da síntese."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import CacheConfig


class AudioCache:
    def __init__(self, cfg: CacheConfig) -> None:
        self.enabled = cfg.enabled
        self.ttl = cfg.ttl_s
        self.dir = Path(cfg.dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(parts: dict[str, Any]) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> tuple[bytes, str] | None:
        meta_path = self.dir / f"{key}.meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - meta.get("created", 0) > self.ttl:
            return None
        audio_path = self.dir / f"{key}.{meta.get('format', 'bin')}"
        if not audio_path.exists():
            return None
        return audio_path.read_bytes(), meta["format"]

    def put(self, key: str, audio: bytes, fmt: str) -> None:
        meta_path = self.dir / f"{key}.meta.json"
        audio_path = self.dir / f"{key}.{fmt}"
        tmp_meta = Path(str(meta_path) + ".tmp")
        tmp_audio = Path(str(audio_path) + ".tmp")
        tmp_audio.write_bytes(audio)
        os.replace(tmp_audio, audio_path)
        tmp_meta.write_text(json.dumps({"format": fmt, "created": time.time()}), encoding="utf-8")
        os.replace(tmp_meta, meta_path)

    def clear(self) -> int:
        n = 0
        for p in self.dir.glob("*.meta.json"):
            stem = p.name[: -len(".meta.json")]
            for f in self.dir.glob(f"{stem}.*"):
                f.unlink(missing_ok=True)
                n += 1
            p.unlink(missing_ok=True)
        return n
