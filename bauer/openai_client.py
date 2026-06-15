"""Cliente para APIs compatíveis com OpenAI /v1/chat/completions.

Suporta: LM Studio (local), Groq, vLLM, OpenAI, qualquer provider com API compatível.
Interface idêntica ao OllamaClient para ser intercambiável no CLI e no servidor.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

# Canônico em ollama_client — manter definição duplicada aqui causou drift real
# (show_model passava context_length/size_bytes que a cópia local não tinha).
from .ollama_client import ModelfileParams


class OpenAIClientError(Exception):
    pass


class OpenAIClient:
    def __init__(
        self,
        host: str = "https://api.openai.com",
        timeout_seconds: int = 60,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        extra_headers: dict[str, str] | None = None,
        api_version: str = "",   # Azure: "2024-08-01-preview" → ?api-version=...
        chat_path: str = "/v1/chat/completions",  # override para providers sem /v1/
    ):
        self.host = host.rstrip("/")
        self.timeout = timeout_seconds
        self.default_model = model
        self._api_version = api_version  # adicionado à query string quando presente
        self._chat_path = chat_path      # caminho base do endpoint de chat
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            self._headers.update(extra_headers)
        # Last `response.usage` payload from the most recent chat call.
        # Populated by chat_stream (via stream_options.include_usage) and
        # chat_with_tools. Empty dict if provider didn't surface usage.
        # Use bauer.account_usage.normalize_usage() to canonicalise.
        self.last_usage: dict = {}

    def _chat_url(self) -> str:
        """URL de chat/completions.

        - Azure (api_version definido): {host}/chat/completions?api-version=...
        - Providers sem /v1/ (copilot, github, gemini): {host}/chat/completions
        - Providers padrão OpenAI-compat: {host}/v1/chat/completions
        """
        if self._api_version:
            return f"{self.host}/chat/completions?api-version={self._api_version}"
        return f"{self.host}{self._chat_path}"

    def is_alive(self) -> tuple[bool, str]:
        """Verifica se a API está respondendo."""
        try:
            r = httpx.get(
                f"{self.host}/v1/models",
                headers=self._headers,
                timeout=self.timeout,
            )
            if r.status_code in (200, 401):
                return True, ""  # 401 = auth error mas API está viva
            return False, f"HTTP {r.status_code} de {self.host}/v1/models"
        except httpx.ConnectError as exc:
            return False, f"Conexao recusada em {self.host} ({exc})"
        except httpx.TimeoutException:
            return False, f"Timeout ({self.timeout}s) conectando em {self.host}"
        except Exception as exc:
            return False, f"Falha inesperada: {exc}"

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.host}/v1/models", headers=self._headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return [m.get("id", "?") for m in data.get("data", [])]
        except Exception as exc:
            raise OpenAIClientError(f"Falha ao listar modelos: {exc}") from exc

    def has_model(self, name: str) -> bool:
        try:
            models = self.list_models()
            # OpenRouter e alguns providers retornam lista vazia — assume disponível
            if not models:
                return True
            return name in models
        except OpenAIClientError:
            return True  # assume disponível se não conseguir listar (ex: Groq, OpenRouter)

    def show_model(self, name: str) -> ModelfileParams:
        return ModelfileParams(num_ctx=None, context_length=None, size_bytes=0, raw={"id": name})

    @property
    def supports_native_tools(self) -> bool:
        """True se este provider suporta native function calling."""
        return True  # todos os providers OpenAI-compat suportam; Ollama não (é OllamaClient)

    def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        """Chamada não-streaming com native function calling.

        Retorna o response completo (choices[0].message) incluindo:
        - content: str | None — texto de resposta (None se fez tool call)
        - tool_calls: list | None — lista de tool calls solicitadas

        Uso:
            response = client.chat_with_tools(model, messages, schemas)
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    result = router.execute_native_call(name, args)
            else:
                text = response.get("content", "")
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": False,
        }
        try:
            resp = httpx.post(
                self._chat_url(),
                json=body,
                headers=self._headers,
                timeout=httpx.Timeout(
                    connect=float(self.timeout),
                    read=300.0,
                    write=10.0,
                    pool=5.0,
                ),
            )
        except httpx.ConnectError as exc:
            raise OpenAIClientError(
                f"Conexao recusada em {self.host}.\nVerifique se o servidor esta rodando."
            ) from exc
        except httpx.TimeoutException:
            raise OpenAIClientError(f"Timeout ({self.timeout}s) em {self.host}.")
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"Erro HTTP: {exc}") from exc

        if resp.status_code >= 400:
            body_text = resp.text[:600]
            raise OpenAIClientError(
                f"[Provedor] HTTP {resp.status_code} em tool calling. Detalhe: {body_text}"
            )

        try:
            data = resp.json()
            # Capture usage for downstream cost accounting. Non-streaming
            # responses always carry it in the top-level `usage` field.
            self.last_usage = dict(data.get("usage") or {})
            return data["choices"][0]["message"]
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise OpenAIClientError(f"Resposta inesperada do provider: {exc}") from exc

    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        """Streaming via /v1/chat/completions com SSE.

        Captures `usage` from the final SSE chunk (when the provider supports
        `stream_options.include_usage=true`) into `self.last_usage`. Providers
        that don't honour the flag (e.g. some older OpenAI-compat backends)
        leave `last_usage = {}` — caller should fall back to an estimate.
        """
        # Reset usage before each call — readers compare to {} to detect "no data".
        self.last_usage = {}
        _error_body: str = ""
        _error_status: int = 0
        _error_exc: httpx.HTTPStatusError | None = None
        try:
            with httpx.stream(
                "POST",
                self._chat_url(),
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    # Request that OpenAI/compat providers include `usage` in the
                    # final SSE event (one chunk before [DONE], with empty choices).
                    # Backends that don't recognise this key ignore it silently.
                    "stream_options": {"include_usage": True},
                },
                headers=self._headers,
                timeout=httpx.Timeout(
                    connect=float(self.timeout),
                    read=300.0,
                    write=10.0,
                    pool=5.0,
                ),
            ) as response:
                if response.status_code >= 400:
                    # Lê o corpo do erro DENTRO do contexto stream (conexão ainda aberta)
                    _error_status = response.status_code
                    try:
                        chunks: list[bytes] = []
                        for chunk in response.iter_bytes():
                            chunks.append(chunk)
                            if sum(len(c) for c in chunks) > 1000:
                                break
                        _error_body = b"".join(chunks).decode("utf-8", errors="replace")[:600]
                    except Exception:
                        _error_body = ""
                    # Constrói HTTPStatusError para compatibilidade com o handler externo
                    _error_exc = httpx.HTTPStatusError(
                        f"HTTP {_error_status}",
                        request=response.request,
                        response=response,
                    )
                else:
                    for line in response.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        # OpenAI's final usage event arrives BEFORE [DONE], shaped
                        # like {"choices": [], "usage": {...}}. Capture it here
                        # so callers can read self.last_usage post-iteration.
                        usage_payload = data.get("usage")
                        if isinstance(usage_payload, dict):
                            self.last_usage = dict(usage_payload)
                        choices = data.get("choices") or []
                        if not choices:
                            # Final usage-only event (no choices) — keep iterating.
                            continue
                        delta = choices[0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            yield chunk
        except httpx.ConnectError as exc:
            raise OpenAIClientError(
                f"Conexao recusada em {self.host}.\n"
                f"Verifique se o servidor esta rodando."
            ) from exc
        except httpx.TimeoutException:
            raise OpenAIClientError(f"Timeout ({self.timeout}s) em {self.host}.")
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"Erro HTTP: {exc}") from exc

        # Trata erros HTTP após sair do contexto stream (corpo já lido acima)
        if _error_exc is not None:
            status = _error_status
            body = _error_body
            if status == 429:
                # Tenta parsear o body para distinguir quota vs rate-limit
                _error_type = ""
                _error_msg = body
                try:
                    _err_json = json.loads(body)
                    _error_type = (
                        _err_json.get("error", {}).get("type", "")
                        or _err_json.get("error", {}).get("code", "")
                    )
                    _error_msg = _err_json.get("error", {}).get("message", body)
                except Exception:
                    pass

                if "insufficient_quota" in _error_type or "insufficient_quota" in body:
                    _hint = (
                        "Sem creditos na conta OpenAI API.\n"
                        f"  Mensagem: {_error_msg}\n\n"
                        "  IMPORTANTE: ChatGPT Plus (assinatura web) != creditos de API.\n"
                        "  Sao produtos separados na OpenAI.\n\n"
                        "  Para resolver — escolha uma opcao:\n"
                        "  A) Adicionar billing em platform.openai.com/settings/billing\n"
                        "     e usar bauer model → OpenAI API Key (sk-...)\n"
                        "  B) Usar Groq gratis: bauer model → Groq → Llama 3.3 70B\n"
                        "  C) Usar Ollama local: bauer model → Ollama"
                    )
                elif "rate_limit" in _error_type or "rate_limit" in body:
                    _hint = (
                        "Rate limit atingido (muitas requisicoes por minuto).\n"
                        f"  Mensagem: {_error_msg}\n"
                        "  - Aguarde alguns segundos e tente novamente\n"
                        "  - Ou troque de provider: bauer model"
                    )
                else:
                    # Erro 429 desconhecido — mostra body completo para diagnóstico
                    _hint = (
                        f"HTTP 429 do provider.\n"
                        f"  Resposta: {_error_msg or body}\n"
                        "  - Verifique sua conta e billing em platform.openai.com\n"
                        "  - Ou troque de provider: bauer model"
                    )
            elif status == 401:
                _code = ""
                try:
                    _code = json.loads(body).get("error", {}).get("code", "")
                except Exception:
                    pass
                if _code == "missing_scope":
                    _hint = (
                        "Token OAuth do ChatGPT nao tem permissao para a API de modelos.\n\n"
                        "  IMPORTANTE: O login 'ChatGPT OAuth' usa sua conta ChatGPT,\n"
                        "  mas a API de completions exige creditos separados de API.\n"
                        "  ChatGPT Plus (assinatura web) != acesso a API developer.\n\n"
                        "  Para resolver — escolha uma opcao:\n"
                        "  A) Usar API Key OpenAI: bauer model → OpenAI API Key (sk-...)\n"
                        "     (requer billing em platform.openai.com/settings/billing)\n"
                        "  B) Usar Groq gratis: bauer model → Groq → Llama 3.3 70B\n"
                        "  C) Usar OpenCode gratis: bauer model → OpenCode Zen\n"
                        "  D) Usar Ollama local: bauer model → Ollama"
                    )
                else:
                    _hint = (
                        "Falha de autenticacao.\n"
                        "  - Verifique se a API key esta correta em config.yaml\n"
                        "  - Rode: bauer model para configurar novamente\n"
                        f"  - URL: {self.host}"
                    )
            elif status == 403:
                _hint = (
                    "Acesso negado.\n"
                    "  - Sua API key pode nao ter acesso a este modelo\n"
                    "  - Verifique os permissoes da chave no provider"
                )
            elif 500 <= status < 600:
                _hint = (
                    "Erro no servidor do provider.\n"
                    "  - Tente novamente em alguns segundos\n"
                    "  - Se persistir, troque de provider: bauer model"
                )
            elif status == 400:
                _hint = (
                    f"Requisicao invalida (HTTP 400).\n"
                    f"  - Verifique se o nome do modelo em config.yaml e valido para este provider\n"
                    f"  - Rode: bauer auth login -p {self.host.split('.')[0].lstrip('https://api.')}\n"
                    f"  - Detalhe: {body}"
                )
            else:
                _hint = f"HTTP {status}. {body}".strip()
            raise OpenAIClientError(f"[Provedor] {_hint}") from _error_exc

    def chat_with_retry(
        self,
        model: str,
        messages: list[dict],
        *,
        max_retries: int = 2,
        on_retry=None,
    ) -> list[str]:
        """Coleta todos os chunks do chat_stream com retry automático.

        Útil quando o caller quer o texto completo e pode aceitar um pequeno delay
        em caso de rate limit ou erro transitório do provider.

        Returns:
            Lista de chunks (strings) — junte com "".join() para o texto completo.
        """
        from .retry_utils import retry_with_backoff

        def _collect() -> list[str]:
            return list(self.chat_stream(model, messages))

        return retry_with_backoff(
            _collect,
            max_retries=max_retries,
            base_delay=5.0,
            max_delay=60.0,
            on_retry=on_retry,
        )
