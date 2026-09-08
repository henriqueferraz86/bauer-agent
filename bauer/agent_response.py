"""Coleta, recuperação e fallback de respostas de provedores LLM."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from .ollama_client import OllamaError
from .openai_client import OpenAIClientError

if TYPE_CHECKING:
    from .context_manager import ContextManager
    from .ollama_client import OllamaClient
    from rich.console import Console


def stream_to_sink(
    client: "OllamaClient", model_name: str, api_payload: list[dict], *,
    max_retries: int = 2, on_retry=None,
) -> list[str]:
    """Consome o stream com retry somente antes da emissão do primeiro token."""
    from .delta_stream import emit_delta, emit_round_start, get_sink
    from .error_classifier import classify_api_error
    from .retry_utils import jittered_backoff

    parts: list[str] = []
    for attempt in range(max_retries + 1):
        emit_round_start()
        parts = []
        try:
            for chunk in client.chat_stream(model_name, api_payload):
                sink = get_sink()
                if sink is not None and getattr(sink, "cancelled", False):
                    from .voice_session import VoiceTurnCancelled

                    raise VoiceTurnCancelled("turno de voz interrompido")
                parts.append(chunk)
                emit_delta(chunk)
                if getattr(sink, "cancelled", False):
                    from .voice_session import VoiceTurnCancelled

                    raise VoiceTurnCancelled("turno de voz interrompido")
            return parts
        except Exception as exc:  # noqa: BLE001 - classificado a seguir
            if parts:
                raise
            classified = classify_api_error(exc)
            if not classified.retryable or attempt >= max_retries:
                raise
            wait = jittered_backoff(attempt, base_delay=5.0, max_delay=60.0)
            if on_retry is not None:
                try:
                    on_retry(attempt + 1, classified, wait)
                except Exception as callback_exc:  # noqa: BLE001
                    from .logging_config import log_suppressed

                    log_suppressed("stream.on_retry", callback_exc)
            time.sleep(wait)
    return parts


def collect_response(
    client: "OllamaClient", model_name: str, payload: list[dict], *,
    stream_to_sink_fn: Callable[..., list[str]] = stream_to_sink,
) -> str:
    """Coleta uma resposta completa, com hooks, cache, custo e sanitização."""
    try:
        from .plugin_hooks import hooks

        hooks.ensure_plugins_loaded()
        hooks.emit("pre_llm_call", model=model_name, messages=payload)
    except Exception:
        pass

    api_payload = payload
    try:
        from .prompt_caching import apply_anthropic_cache_control, should_apply_cache_control

        if should_apply_cache_control(client):
            api_payload = apply_anthropic_cache_control(payload)
    except Exception:
        api_payload = payload

    from .delta_stream import get_sink
    from .openai_client import OpenAIClient as OpenAIClient

    if get_sink() is not None:
        parts = stream_to_sink_fn(client, model_name, api_payload)
    elif isinstance(client, OpenAIClient) and hasattr(client, "chat_with_retry"):
        parts = client.chat_with_retry(model_name, api_payload)
    else:
        parts = list(client.chat_stream(model_name, api_payload))
    response = "".join(parts)

    try:
        from .unicode_utils import sanitize_surrogates

        response = sanitize_surrogates(response)
    except Exception:
        response = response.encode("utf-8", errors="replace").decode("utf-8")

    try:
        from .plugin_hooks import hooks

        hooks.emit("post_llm_call", model=model_name, messages=payload, response=response)
    except Exception:
        pass

    try:
        from .cost_meter import provider_from_client, report_llm_cost

        report_llm_cost(provider_from_client(client), model_name, getattr(client, "last_usage", None))
    except Exception:
        pass

    try:
        import logging

        from .secrets_scanner import scan

        scan_result = scan(response, redact=True)
        if scan_result.found:
            names = ", ".join(set(match["name"] for match in scan_result.matches))
            logging.getLogger(__name__).warning(
                "[secrets_scanner] Segredos na resposta do modelo: %s. Redagidos.", names
            )
            response = scan_result.redacted_text
    except Exception:
        pass

    return response


def recover_empty_response(
    client: "OllamaClient", model_name: str, ctx: "ContextManager",
    console: "Console | None" = None, *,
    collect_response_fn: Callable[..., str] = collect_response,
) -> tuple[str, str]:
    """Recupera resposta vazia com retry e compressão de contexto."""
    time.sleep(2.0)
    response = collect_response_fn(client, model_name, ctx.get_payload())
    if response.strip():
        return response, ""

    compressed = False
    try:
        compressed = ctx.force_compress()
    except Exception:
        compressed = False
    if compressed:
        if console is not None:
            console.print("[dim][recovery] contexto comprimido — tentando novamente...[/dim]")
        response = collect_response_fn(client, model_name, ctx.get_payload())
        if response.strip():
            return response, ""

    payload = ctx.get_payload()
    approx_chars = sum(
        len(message.get("content", "") if isinstance(message.get("content"), str)
            else str(message.get("content", "")))
        for message in payload
    )
    approx_tokens = approx_chars // 4
    applied = getattr(ctx, "applied_context", 0) or 0
    pct = f" (~{approx_tokens * 100 // applied}% do contexto)" if applied else ""

    try:
        from .incidents import record_incident

        record_incident(
            "empty_response", model=model_name, provider=getattr(ctx, "provider", "?"),
            messages_count=len(payload), approx_tokens=approx_tokens,
            applied_context=applied, compressed_before_final_retry=compressed,
        )
    except Exception:
        pass

    diagnostic = (
        "[Modelo retornou resposta vazia mesmo após retry + compressão]\n"
        f"  Modelo: {model_name}\n"
        f"  Contexto: {len(payload)} mensagens, ~{approx_tokens:,} tokens{pct}\n"
        "  Prováveis causas:\n"
        "    1. Rate-limit silencioso do provider (comum em free tier)\n"
        "    2. Filtro de conteúdo bloqueando a resposta\n"
        "    3. Modelo sobrecarregado no servidor\n"
        "  Soluções:\n"
        "    Aguarde 30s — pode ser rate-limit transiente\n"
        "    /model      — troca de provider/modelo\n"
        "    /clear      — última opção: limpa todo o histórico"
    )
    return "", diagnostic


def parse_provider_context_cap(error_text: str) -> int | None:
    """Extrai uma janela de contexto explícita de mensagens de erro de API."""
    import re

    match = re.search(r"maximum context length is (\d{3,})", error_text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    match = re.search(r"context length of only (\d{3,})|context window of (\d{3,})", error_text)
    if match:
        try:
            return int(match.group(1) or match.group(2))
        except ValueError:
            return None
    return None


def collect_with_fallback(
    client: "OllamaClient", model_name: str, payload: list[dict], fallback_clients,
    console: "Console", streamer=None, *,
    collect_response_fn: Callable[..., str] = collect_response,
    thinking_status: Callable[..., Any],
) -> tuple[str, Any, str]:
    """Coleta resposta e troca de provider apenas para falhas recuperáveis."""
    def collect_with_stream_sink(active_client, active_model):
        if streamer is None:
            return collect_response_fn(active_client, active_model, payload)
        from .delta_stream import reset_sink, set_sink

        token = set_sink(streamer)
        try:
            return collect_response_fn(active_client, active_model, payload)
        finally:
            reset_sink(token)

    try:
        from .cost_meter import provider_from_client

        primary_provider = provider_from_client(client)
    except Exception:
        primary_provider = getattr(client, "_provider", None) or "openai"

    try:
        from .circuit_breaker import global_cb

        circuit_breaker_available = True
    except Exception:
        circuit_breaker_available = False
        global_cb = None  # type: ignore[assignment]

    primary_failed_exc: Exception | None = None
    if circuit_breaker_available and global_cb is not None and global_cb.is_open(primary_provider):
        console.print(
            f"[yellow]⚡ Circuit OPEN para '{primary_provider}' — saltando para fallback[/yellow]"
        )
    else:
        try:
            with thinking_status(console, model_name):
                response = collect_with_stream_sink(client, model_name)
            if circuit_breaker_available and global_cb is not None:
                global_cb.record_success(primary_provider)
            return response, client, model_name
        except (OllamaError, OpenAIClientError) as primary_exc:
            if circuit_breaker_available and global_cb is not None:
                global_cb.record_failure(primary_provider, primary_exc)
            if not fallback_clients:
                raise
            should_fallback = True
            try:
                from .error_classifier import classify_api_error

                should_fallback = classify_api_error(primary_exc).should_fallback
            except Exception:
                pass
            if not should_fallback:
                raise
            primary_failed_exc = primary_exc
    if not fallback_clients:
        if primary_failed_exc is not None:
            raise primary_failed_exc
        raise OllamaError("Circuit OPEN e sem fallback configurado")

    for fallback_entry in fallback_clients:
        fallback_client, fallback_model = fallback_entry[0], fallback_entry[1]
        fallback_label = (
            fallback_entry[2] if len(fallback_entry) > 2
            else getattr(fallback_client, "default_model", fallback_model)
        )
        try:
            from .cost_meter import provider_from_client

            fallback_provider = provider_from_client(fallback_client)
        except Exception:
            fallback_provider = getattr(fallback_client, "_provider", None) or "openai"
        if circuit_breaker_available and global_cb is not None and global_cb.is_open(fallback_provider):
            console.print(f"[dim]  Fallback {fallback_label}: circuit OPEN, pulando[/dim]")
            continue
        console.print(
            f"[yellow]⚡ Provider falhou — tentando fallback: [bold]{fallback_label}[/bold][/yellow]"
        )
        try:
            with thinking_status(console, fallback_model):
                response = collect_with_stream_sink(fallback_client, fallback_model)
            if circuit_breaker_available and global_cb is not None:
                global_cb.record_success(fallback_provider)
            return response, fallback_client, fallback_model
        except Exception as fallback_exc:
            if circuit_breaker_available and global_cb is not None:
                global_cb.record_failure(fallback_provider, fallback_exc)
            should_compress = False
            try:
                from .error_classifier import classify_api_error

                should_compress = classify_api_error(fallback_exc).should_compress
            except Exception:
                pass
            if should_compress:
                console.print(
                    "[yellow]⚠ Payload grande demais para os providers — "
                    "interrompendo a varredura de fallbacks para comprimir o contexto.[/yellow]"
                )
                raise
            console.print(f"[dim]  Fallback {fallback_label} também falhou: {fallback_exc}[/dim]")

    if primary_failed_exc is not None:
        raise primary_failed_exc
    raise OllamaError("Todos os providers estão com circuit OPEN ou falharam")
