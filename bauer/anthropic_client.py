"""Cliente nativo para a API Anthropic (Claude).

Wire protocol diferente do OpenAI:
  - Auth: header  x-api-key  (não Authorization: Bearer)
  - Versão: header  anthropic-version
  - Endpoint: https://api.anthropic.com/v1/messages
  - Request:  {"model", "max_tokens", "messages": [{"role","content"}]}
  - Response: {"content": [{"type":"text","text":"..."}], ...}
  - Streaming: SSE com event:content_block_delta / delta.text

Interface idêntica ao OllamaClient / OpenAIClient para ser intercambiável.

Modelos recomendados:
  claude-3-5-sonnet-20241022   — melhor custo/benefício (recomendado)
  claude-3-5-haiku-20241022    — rápido e barato
  claude-3-opus-20240229       — máxima capacidade
  claude-3-haiku-20240307      — mínimo custo
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

_ENDPOINT = "https://api.anthropic.com/v1"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicClientError(Exception):
    pass


@dataclass
class ModelfileParams:
    """Stub para compatibilidade com interface OllamaClient."""
    num_ctx: int | None
    raw: dict[str, Any]


class AnthropicClient:
    """Cliente HTTP para Anthropic Messages API.

    Compatível com a interface esperada pelo CLI (is_alive, has_model,
    show_model, chat_stream).
    """

    def __init__(
        self,
        api_key: str = "",
        timeout_seconds: int = 60,
        api_version: str = "2023-06-01",
        model: str = "claude-3-5-haiku-20241022",
    ):
        self.default_model = model
        self.timeout = timeout_seconds
        self._api_version = api_version
        self._headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": api_version,
        }

    # ── Interface pública (compatível com OllamaClient) ──────────────────────

    def is_alive(self) -> tuple[bool, str]:
        """Verifica se a API está respondendo (testa /v1/models)."""
        try:
            r = httpx.get(
                f"{_ENDPOINT}/models",
                headers=self._headers,
                timeout=10.0,
            )
            if r.status_code in (200, 401, 403):
                return True, ""
            return False, f"HTTP {r.status_code} em {_ENDPOINT}/models"
        except httpx.ConnectError as exc:
            return False, f"Conexao recusada em {_ENDPOINT} ({exc})"
        except httpx.TimeoutException:
            return False, f"Timeout ({self.timeout}s) conectando em {_ENDPOINT}"
        except Exception as exc:
            return False, f"Falha: {exc}"

    def list_models(self) -> list[str]:
        """Lista modelos disponíveis via /v1/models."""
        try:
            r = httpx.get(f"{_ENDPOINT}/models", headers=self._headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return [m.get("id", "?") for m in data.get("data", [])]
        except Exception as exc:
            raise AnthropicClientError(f"Falha ao listar modelos: {exc}") from exc

    def has_model(self, name: str) -> bool:
        try:
            models = self.list_models()
            if not models:
                return True
            return name in models
        except AnthropicClientError:
            return True

    def show_model(self, name: str) -> ModelfileParams:
        return ModelfileParams(num_ctx=None, raw={"id": name})

    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        """Streaming via /v1/messages com SSE Anthropic."""
        # Anthropic não suporta role "system" dentro de messages[]
        # — deve ir no campo "system" do request body.
        system_content = ""
        filtered: list[dict] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content += msg.get("content", "") + "\n"
            else:
                filtered.append(msg)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": filtered,
            "stream": True,
        }
        if system_content.strip():
            payload["system"] = system_content.strip()

        try:
            with httpx.stream(
                "POST",
                f"{_ENDPOINT}/messages",
                json=payload,
                headers=self._headers,
                timeout=httpx.Timeout(
                    connect=float(self.timeout),
                    read=300.0,
                    write=10.0,
                    pool=5.0,
                ),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[5:].strip()
                    if payload_str in ("[DONE]", ""):
                        continue
                    try:
                        data = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                    elif event_type == "message_stop":
                        break
                    elif event_type == "error":
                        err = data.get("error", {})
                        raise AnthropicClientError(
                            f"Erro da API Anthropic: {err.get('type')} — {err.get('message')}"
                        )

        except httpx.ConnectError as exc:
            raise AnthropicClientError(
                f"Conexao recusada em {_ENDPOINT}.\n"
                f"Verifique sua ANTHROPIC_API_KEY e conexao com a internet."
            ) from exc
        except httpx.TimeoutException:
            raise AnthropicClientError(f"Timeout ({self.timeout}s) em {_ENDPOINT}.")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            try:
                exc.response.read()
                body = exc.response.text[:300]
            except Exception:
                body = "(sem corpo)"
            _ERROS = {
                401: "API key inválida. Verifique ANTHROPIC_API_KEY.",
                403: "Sem permissão para este modelo.",
                429: "Rate limit atingido. Aguarde e tente novamente.",
                529: "API Anthropic sobrecarregada. Tente novamente em instantes.",
            }
            msg = _ERROS.get(status, f"HTTP {status}: {body}")
            raise AnthropicClientError(msg) from exc
        except httpx.HTTPError as exc:
            raise AnthropicClientError(f"Erro HTTP: {exc}") from exc
