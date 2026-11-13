"""Autenticação por chave de API e limite de requisições por cliente."""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import deque

from .config import AuthConfig

log = logging.getLogger("voxrouter.auth")


class AuthError(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RateLimiter:
    """Janela deslizante de 60s por cliente."""

    def __init__(self, window_s: float = 60.0) -> None:
        self.window = window_s
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, client: str, rpm: int | None) -> int | None:
        """Registra a chamada; devolve None se permitida, ou segundos até liberar."""
        if not rpm:
            return None
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(client, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= rpm:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            return None


class Authenticator:
    def __init__(self, cfg: AuthConfig) -> None:
        self.enabled = cfg.enabled
        self.default_rpm = cfg.default_rpm
        self.limiter = RateLimiter()
        self._keys: list[tuple[str, str, int | None]] = []
        for k in cfg.keys:
            value = k.key or (os.environ.get(k.key_env) if k.key_env else None)
            if not value:
                log.warning("auth: chave '%s' ignorada (variável %s vazia)", k.name, k.key_env)
                continue
            self._keys.append((k.name, value, k.rpm))
        if self.enabled and not self._keys:
            log.warning("auth: habilitada mas nenhuma chave válida — todas as chamadas serão negadas")

    def identify(self, token: str | None) -> tuple[str, int | None] | None:
        if not token:
            return None
        found = None
        for name, value, rpm in self._keys:  # sem retorno antecipado: tempo constante
            if hmac.compare_digest(token.encode(), value.encode()):
                found = (name, rpm)
        return found

    def check(self, authorization: str | None, x_api_key: str | None) -> str:
        """Valida o cliente; devolve o nome dele ou lança AuthError (401/429)."""
        if not self.enabled:
            return "anonimo"
        token = x_api_key
        if not token and authorization:
            scheme, _, value = authorization.partition(" ")
            token = value.strip() if scheme.lower() == "bearer" else None
        who = self.identify(token)
        if who is None:
            raise AuthError(401, "chave de API ausente ou inválida (use 'Authorization: Bearer <chave>')")
        name, rpm = who
        retry = self.limiter.check(name, rpm or self.default_rpm)
        if retry is not None:
            raise AuthError(429, f"limite de requisições excedido para '{name}'", retry_after=retry)
        return name
