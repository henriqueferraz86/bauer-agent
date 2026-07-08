"""Cliente para o backend do ChatGPT (Responses API) — login via browser.

Usa o token OAuth da conta ChatGPT (Plus/Pro/Team) para chamar o endpoint
`/responses`, billando na assinatura — igual ao Codex CLI. NÃO usa créditos
de API (sk-...).

Experimental: depende do backend do ChatGPT, não da API pública. Requer
assinatura ChatGPT ativa. Endpoint configurável via `openai.chatgpt_base_url`.

Formato da Responses API difere de /chat/completions:
  - request: {model, instructions, input:[{type:message, role, content:[...]}], stream}
  - SSE: eventos `response.output_text.delta` carregam o texto incremental.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from .http_shared import shared_ssl_context

from .openai_client import OpenAIClient, OpenAIClientError


# Endpoint padrão usado pelo Codex CLI (billa na assinatura ChatGPT).
DEFAULT_CHATGPT_BASE = "https://chatgpt.com/backend-api/codex"


class ChatGPTBackendClient(OpenAIClient):
    """Subclasse do OpenAIClient que fala a Responses API do backend ChatGPT.

    Reaproveita is_alive/list_models/show_model/chat_with_retry do pai, mas
    sobrescreve chat_stream para o formato Responses e usa headers do Codex.
    """

    def __init__(
        self,
        access_token: str,
        account_id: str | None = None,
        *,
        base_url: str = DEFAULT_CHATGPT_BASE,
        timeout_seconds: int = 60,
        model: str = "codex-mini-latest",
    ):
        super().__init__(
            host=base_url,
            timeout_seconds=timeout_seconds,
            api_key=access_token,
            model=model,
        )
        self._account_id = account_id or ""
        # Headers que o backend do ChatGPT espera (estilo Codex CLI).
        self._headers["OpenAI-Beta"] = "responses=experimental"
        self._headers["originator"] = "codex_cli_rs"
        self._headers["session_id"] = str(uuid.uuid4())
        if self._account_id:
            self._headers["chatgpt-account-id"] = self._account_id

    # ── A Responses API não expõe /v1/models neste backend ──────────────────
    def is_alive(self) -> tuple[bool, str]:
        # Sem endpoint de health barato — assume vivo; erros aparecem no chat.
        return True, ""

    def list_models(self) -> list[str]:
        # O endpoint /backend-api/codex/responses só aceita modelos Codex.
        return [
            "codex-mini-latest",
            "o4-mini",
            "o3-mini",
        ]

    def has_model(self, name: str) -> bool:
        return True

    @property
    def supports_native_tools(self) -> bool:
        # Usa o bridge de tools por texto (o agent já tem fallback). Evita
        # traduzir o formato de tool calling da Responses API nesta versão.
        return False

    # ── Tradução chat/completions → Responses API ───────────────────────────
    @staticmethod
    def _to_responses_input(messages: list[dict]) -> tuple[str, list[dict]]:
        """Separa o system prompt (instructions) e converte o resto p/ `input`."""
        instructions = ""
        items: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not isinstance(content, str):
                # content já em partes (multimodal) — serializa texto simples
                content = json.dumps(content, ensure_ascii=False)
            if role == "system":
                instructions = (instructions + "\n\n" + content).strip() if instructions else content
                continue
            # assistant usa output_text; user/tool usam input_text
            part_type = "output_text" if role == "assistant" else "input_text"
            items.append({
                "type": "message",
                "role": role if role in ("user", "assistant") else "user",
                "content": [{"type": part_type, "text": content}],
            })
        return instructions, items

    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        """Streaming via Responses API. Yields deltas de texto."""
        self.last_usage = {}
        instructions, input_items = self._to_responses_input(messages)
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "input": input_items,
            "stream": True,
            "store": False,
        }
        if instructions:
            body["instructions"] = instructions

        url = f"{self.host}/responses"
        _err_status = 0
        _err_body = ""
        try:
            with httpx.stream(
                "POST",
                url,
                json=body,
                headers=self._headers,
                timeout=httpx.Timeout(connect=float(self.timeout), read=300.0, write=10.0, pool=5.0),
                verify=shared_ssl_context(),
            ) as response:
                if response.status_code >= 400:
                    _err_status = response.status_code
                    try:
                        chunks: list[bytes] = []
                        for chunk in response.iter_bytes():
                            chunks.append(chunk)
                            if sum(len(c) for c in chunks) > 1200:
                                break
                        _err_body = b"".join(chunks).decode("utf-8", errors="replace")[:800]
                    except Exception:
                        _err_body = ""
                else:
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            evt = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        etype = evt.get("type", "")
                        # Texto incremental
                        if etype == "response.output_text.delta":
                            delta = evt.get("delta", "")
                            if delta:
                                yield delta
                        # Usage no evento final
                        elif etype == "response.completed":
                            usage = (evt.get("response") or {}).get("usage")
                            if isinstance(usage, dict):
                                self.last_usage = dict(usage)
                        elif etype == "error":
                            err = evt.get("error") or evt
                            raise OpenAIClientError(
                                f"[ChatGPT] Erro no stream: {err.get('message', err)}"
                            )
        except httpx.ConnectError as exc:
            raise OpenAIClientError(
                f"Conexao recusada em {self.host}.\nVerifique sua conexao."
            ) from exc
        except httpx.TimeoutException:
            raise OpenAIClientError(f"Timeout ({self.timeout}s) em {self.host}.")
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"Erro HTTP: {exc}") from exc

        if _err_status:
            if _err_status in (401, 403):
                raise OpenAIClientError(
                    "[ChatGPT] Token OAuth invalido ou expirado.\n"
                    "  - Refaca o login: bauer auth login -p openai\n"
                    "  - Confirme que sua assinatura ChatGPT (Plus/Pro) esta ativa\n"
                    f"  - Detalhe: {_err_body}"
                )
            raise OpenAIClientError(
                f"[ChatGPT] HTTP {_err_status} no backend.\n  Detalhe: {_err_body}"
            )
