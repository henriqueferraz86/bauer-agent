"""Configuração de logging para o Bauer Agent.

Logs vão para arquivo e console com formatação simples e clara.
Premortem item 9: todo erro precisa ter causa, valor configurado, valor detectado
e ação sugerida. Logs aqui são o canal pra isso.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logging(level: str = "info", file_path: str | None = None) -> logging.Logger:
    """Configura o logger raiz do Bauer. Idempotente."""
    logger = logging.getLogger("bauer")
    logger.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    logger.propagate = False

    # Evita handlers duplicados em chamadas repetidas (testes, REPL).
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if file_path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "bauer") -> logging.Logger:
    return logging.getLogger(name)


def log_suppressed(context: str, exc: BaseException, *, logger_name: str = "bauer") -> None:
    """Loga uma excecao suprimida em DEBUG para diagnosabilidade.

    Use em lugar de `except Exception: pass` quando a supressao e intencional
    mas voce quer rastro em modo debug. O chamador nao e interrompido.

    Exemplo:
        except Exception as exc:
            log_suppressed("learning_engine.append_entry", exc)
    """
    log = logging.getLogger(logger_name)
    log.debug("[suprimido] %s: %s(%s)", context, type(exc).__name__, exc)
