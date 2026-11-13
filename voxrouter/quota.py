"""Gerenciador de cotas: consumo de caracteres por provedor, persistido em disco.

Semântica (inspirada no quota-aware do OmniRoute):
- Consumo só conta em síntese BEM-SUCEDIDA (falha e cache hit não consomem).
- Período é de calendário (mês = "2026-11", dia = "2026-11-13"); ao virar o
  período o contador recomeça sozinho.
- status: unlimited | ok | low (dentro da reserva) | exhausted.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import QuotaConfig


class QuotaManager:
    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(self.state_path) + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

    @staticmethod
    def period_key(period: str, now: datetime | None = None) -> str:
        dt = now or datetime.now()
        return dt.strftime("%Y-%m") if period == "month" else dt.strftime("%Y-%m-%d")

    def used(self, name: str, q: QuotaConfig) -> int:
        return self._data.get(name, {}).get(self.period_key(q.period), 0)

    def remaining(self, name: str, q: QuotaConfig) -> int:
        return max(0, q.limit - self.used(name, q))

    def status(self, name: str, q: QuotaConfig) -> str:
        used = self.used(name, q)
        if used >= q.limit:
            return "exhausted"
        if q.reserve > 0 and q.limit - used <= q.limit * q.reserve:
            return "low"
        return "ok"

    def consume(self, name: str, q: QuotaConfig, n_chars: int) -> None:
        with self._lock:
            pk = self.period_key(q.period)
            entry = self._data.setdefault(name, {})
            entry[pk] = entry.get(pk, 0) + n_chars
            self._save()

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name:
                self._data.pop(name, None)
            else:
                self._data.clear()
            self._save()

    def snapshot(self, providers_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, pcfg in providers_cfg.items():
            q = pcfg.quota
            if q is None:
                out[name] = {"quota": None, "status": "unlimited"}
            else:
                out[name] = {
                    "limit": q.limit,
                    "period": q.period,
                    "reserve": q.reserve,
                    "used": self.used(name, q),
                    "remaining": self.remaining(name, q),
                    "status": self.status(name, q),
                }
        return out
