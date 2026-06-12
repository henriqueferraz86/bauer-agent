"""Registry de bridges vivos — tools mandam mensagem nos chats REAIS.

Quando o gateway está rodando, os bridges (Telegram, Discord, …) ficam
registrados aqui; a tool ``send_message`` do ToolRouter entrega direto pelo
bridge — chega no chat na hora, com mídia, sem passar pelo outbox.

Quando o gateway NÃO está no processo (ex.: ``bauer agent`` no CLI), o
registry está vazio e a tool cai para o ``GatewayOutbox`` (entrega na
próxima vez que o gateway subir).

Thread-safe; process-local de propósito — um registro distribuído seria
infra demais para o problema.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("bauer.live_bridges")

_lock = threading.Lock()
_bridges: dict[str, Any] = {}


def register(name: str, bridge: Any) -> None:
    """Registra um bridge vivo (chamado pelo gateway_runtime no start)."""
    with _lock:
        _bridges[name] = bridge
    logger.debug("Bridge %s registrado para envio direto", name)


def unregister(name: str) -> None:
    with _lock:
        _bridges.pop(name, None)


def get(name: str) -> Any | None:
    """Bridge vivo pelo nome do canal ('telegram', 'discord') ou None."""
    with _lock:
        return _bridges.get(name)


def names() -> list[str]:
    with _lock:
        return sorted(_bridges)


def clear() -> None:
    """Limpa o registry (testes / shutdown)."""
    with _lock:
        _bridges.clear()
