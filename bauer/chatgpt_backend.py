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
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from .http_shared import shared_ssl_context
from .logging_config import log_suppressed

from .openai_client import OpenAIClient, OpenAIClientError


# Endpoint padrão usado pelo Codex CLI (billa na assinatura ChatGPT).
DEFAULT_CHATGPT_BASE = "https://chatgpt.com/backend-api/codex"

# Candidatos que JÁ existiram ou podem existir neste backend. Não é a resposta —
# é só o conjunto que a sonda testa.
#
# Por que sondar em vez de manter lista fixa: o token OAuth do login ChatGPT
# tem escopos `openid profile email offline_access api.connectors.*` e o
# `GET /v1/models` devolve **403 missing scopes: api.model.read**. Não existe
# listagem. A lista fixa anterior (codex-mini-latest / o4-mini / o3-mini)
# apodreceu inteira: medido em 2026-08-07, os TRÊS respondem
# "not supported when using Codex with a ChatGPT account" — inclusive o que
# estava marcado como recomendado. Só `gpt-5.5` passou, de 31 testados.
CHATGPT_MODEL_CANDIDATES: tuple[str, ...] = (
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.5-mini", "gpt-5.5-codex",
    "gpt-5.6", "gpt-6",
    "gpt-5.2", "gpt-5.2-codex", "gpt-5.1", "gpt-5.1-codex",
    "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
    "gpt-5", "gpt-5-codex", "gpt-5-codex-mini",
    "codex-mini-latest", "o4-mini", "o3-mini", "o3",
)

# Fallback quando a sonda não pôde rodar (sem rede, sem token). Reflete a
# medição de 2026-08-07 — melhor que a lista morta, pior que sondar.
CHATGPT_FALLBACK_MODELS: tuple[str, ...] = ("gpt-5.5",)

_CACHE_TTL_SECONDS = 24 * 3600


def _codex_headers(access_token: str, account_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "session_id": str(uuid.uuid4()),
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _model_is_accepted(
    model: str, headers: dict[str, str], base_url: str, timeout: float
) -> bool:
    """Pergunta ao backend se ele aceita o modelo.

    Manda o menor request possível e fecha o stream assim que sabe o status —
    modelo aceito custa alguns tokens da assinatura, não uma resposta inteira.
    """
    body = {
        "model": model,
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
        "store": False,
    }
    try:
        with httpx.stream(
            "POST", f"{base_url}/responses", json=body, headers=headers,
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=10.0, pool=5.0),
            verify=shared_ssl_context(),
        ) as response:
            return response.status_code < 400
    except Exception:
        return False


def probe_supported_models(
    access_token: str,
    account_id: str | None = None,
    *,
    base_url: str = DEFAULT_CHATGPT_BASE,
    candidates: Sequence[str] | None = None,
    timeout: float = 20.0,
    max_workers: int = 6,
) -> list[str]:
    """Descobre quais modelos ESTA conta aceita, na ordem de `candidates`."""
    names = list(candidates or CHATGPT_MODEL_CANDIDATES)
    headers = _codex_headers(access_token, account_id)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        verdicts = list(pool.map(
            lambda m: _model_is_accepted(m, headers, base_url, timeout), names
        ))
    return [name for name, ok in zip(names, verdicts) if ok]


def _cache_path() -> Path:
    from .paths import get_bauer_home
    return get_bauer_home() / "chatgpt_models.json"


def discover_models(
    access_token: str,
    account_id: str | None = None,
    *,
    base_url: str = DEFAULT_CHATGPT_BASE,
    force: bool = False,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> list[str]:
    """Modelos aceitos, com cache em disco (a sonda custa ~20 requests)."""
    path = _cache_path()
    key = account_id or "default"

    if not force:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            entry = cached.get(key) or {}
            fresh = (time.time() - float(entry.get("checked_at", 0))) < ttl_seconds
            if fresh and entry.get("models"):
                return list(entry["models"])
        except (OSError, ValueError, TypeError) as exc:
            log_suppressed("chatgpt_backend.discover_models/read_cache", exc)

    models = probe_supported_models(access_token, account_id, base_url=base_url)
    if not models:
        # Sonda vazia = rede caiu ou token expirou. Não gravar: cachear vazio
        # deixaria o menu sem nenhuma opção por 24h.
        return list(CHATGPT_FALLBACK_MODELS)

    try:
        existing = {}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing[key] = {"models": models, "checked_at": time.time()}
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        # Cache é otimização, nunca motivo de falha.
        log_suppressed("chatgpt_backend.discover_models/write_cache", exc)
    return models


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
        # O pai só guarda o token dentro do header Authorization; list_models
        # precisa dele solto para sondar.
        self._access_token = access_token
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
        """Modelos que ESTA conta aceita (sondados, com cache de 24h)."""
        return discover_models(
            self._access_token, self._account_id or None, base_url=self.host
        )

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
