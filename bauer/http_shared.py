"""SSL context compartilhado para chamadas httpx module-level.

Cada `httpx.get/post/stream(...)` (e cada `httpx.Client()`) constrói um
SSL context novo, e `load_verify_locations` custa ~260ms no Windows.
Multiplicado pelos call sites quentes (uma chamada LLM por turno, um
client de fallback por modelo configurado no startup), isso dominava
tanto o tempo de inicialização do `bauer agent` quanto a latência por
mensagem. Um único context é thread-safe para leitura e pode ser
reusado por todas as conexões — basta passar `verify=shared_ssl_context()`.
"""

from __future__ import annotations

import ssl
import threading

_lock = threading.Lock()
_context: "ssl.SSLContext | None" = None


def shared_ssl_context() -> ssl.SSLContext:
    """Retorna o SSL context default do httpx, criado uma única vez."""
    global _context
    if _context is None:
        with _lock:
            if _context is None:
                import httpx
                _context = httpx.create_ssl_context()
    return _context
