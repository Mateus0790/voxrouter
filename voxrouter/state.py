"""Circuit breaker e estatísticas por provedor."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


class CircuitBreaker:
    """Após falhas seguidas, abre por cooldown crescente (evita martelar provedor caído)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._until: dict[str, float] = {}

    def available(self, name: str) -> bool:
        return time.monotonic() >= self._until.get(name, 0.0)

    def record_failure(self, name: str, cooldown_s: float) -> None:
        with self._lock:
            f = self._failures.get(name, 0) + 1
            self._failures[name] = f
            self._until[name] = time.monotonic() + min(cooldown_s * f, 300.0)

    def record_success(self, name: str) -> None:
        with self._lock:
            self._failures[name] = 0
            self._until[name] = 0.0

    def snapshot(self, name: str) -> dict:
        remaining = max(0.0, self._until.get(name, 0.0) - time.monotonic())
        return {
            "failures": self._failures.get(name, 0),
            "available": self.available(name),
            "cooldown_remaining_s": round(remaining, 1),
        }


@dataclass
class _ProviderStat:
    calls: int = 0
    ok: int = 0
    fail: int = 0
    latency_total_ms: int = 0
    last_error: str = ""


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._providers: dict[str, _ProviderStat] = {}

    def _p(self, name: str) -> _ProviderStat:
        if name not in self._providers:
            self._providers[name] = _ProviderStat()
        return self._providers[name]

    def record_ok(self, name: str, latency_ms: int) -> None:
        with self._lock:
            p = self._p(name)
            p.calls += 1
            p.ok += 1
            p.latency_total_ms += latency_ms

    def record_fail(self, name: str, error: str) -> None:
        with self._lock:
            p = self._p(name)
            p.calls += 1
            p.fail += 1
            p.last_error = error[:200]

    def snapshot(self) -> dict:
        with self._lock:
            providers = {}
            for name, p in self._providers.items():
                providers[name] = {
                    "calls": p.calls,
                    "ok": p.ok,
                    "fail": p.fail,
                    "avg_latency_ms": round(p.latency_total_ms / p.ok) if p.ok else None,
                    "last_error": p.last_error or None,
                }
            return {
                "requests": self.requests,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "providers": providers,
            }
