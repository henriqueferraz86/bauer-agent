"""Utilitários de retry com exponential backoff e jitter.

Inspirado no retry_utils.py do Hermes Agent v0.14.0.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Optional, TypeVar

from .error_classifier import ClassifiedError, FailReason, classify_api_error

T = TypeVar("T")

# Contador global para decorrelação de jitter (similar ao Hermes)
_jitter_counter: int = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Calcula delay com exponential backoff e jitter decorrelado.

    Args:
        attempt: número do retry (1-based).
        base_delay: delay base em segundos para attempt=1.
        max_delay: cap máximo em segundos.
        jitter_ratio: fração do delay usada como jitter aleatório.
            0.5 → jitter uniforme em [0, 0.5 * delay].

    Returns:
        Delay em segundos: min(base * 2^(attempt-1), max_delay) + jitter.

    O jitter decorrelado evita thundering herd quando múltiplas sessões
    atingem o mesmo provider ao mesmo tempo.
    """
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 62 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    # Seed com tempo + contador para decorrelação mesmo com clocks imprecisos
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)
    return delay + jitter


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    approx_tokens: int = 0,
    context_length: int = 200_000,
    on_retry: Optional[Callable[[int, ClassifiedError, float], None]] = None,
    interrupt_check: Optional[Callable[[], bool]] = None,
) -> T:
    """Executa fn() com retry automático para erros retryable.

    Args:
        fn: função sem argumentos que pode levantar exceção.
        max_retries: máximo de tentativas adicionais (0 = sem retry).
        base_delay: delay base para backoff (segundos).
        max_delay: cap máximo de delay (segundos).
        approx_tokens: estimativa de tokens no payload (para classificar overflow).
        context_length: janela de contexto do provider atual.
        on_retry: callback(attempt, classified_error, wait_secs) chamado antes de cada retry.
        interrupt_check: callable que retorna True se execução deve ser abortada.

    Returns:
        Resultado de fn() na primeira tentativa bem-sucedida.

    Raises:
        Exception: última exceção se todas as tentativas falharem,
                   ou imediatamente para erros não-retryable (auth, quota, modelo).
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        # Verifica interrupt antes de tentar
        if interrupt_check and interrupt_check():
            raise RuntimeError("Execução interrompida pelo usuário.")

        try:
            return fn()
        except Exception as exc:
            classified = classify_api_error(
                exc,
                approx_tokens=approx_tokens,
                context_length=context_length,
            )
            last_error = exc

            # Erros não-retryable: re-raise imediatamente
            if not classified.retryable:
                raise

            # Última tentativa: re-raise
            if attempt >= max_retries:
                raise

            # Calcula delay e notifica caller
            wait = jittered_backoff(
                attempt + 1,
                base_delay=base_delay,
                max_delay=max_delay,
            )

            if on_retry:
                on_retry(attempt + 1, classified, wait)

            # Sleep em incrementos pequenos para responder a interrupções
            sleep_end = time.monotonic() + wait
            while time.monotonic() < sleep_end:
                if interrupt_check and interrupt_check():
                    raise RuntimeError("Execução interrompida durante backoff.")
                time.sleep(0.2)

    # Nunca deve chegar aqui, mas satisfaz o type checker
    if last_error:
        raise last_error
    raise RuntimeError("retry_with_backoff: estado inesperado")
