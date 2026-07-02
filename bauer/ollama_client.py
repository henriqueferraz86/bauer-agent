"""Cliente HTTPX para Ollama.

Premortem item 3: o Ollama pode mentir sobre contexto. O doctor precisa testar de
verdade. Este cliente expõe as três leituras baratas (Decisão 2 — Camada A):
  - `is_alive()`              → Ollama está respondendo?
  - `list_models()`           → quais modelos existem?
  - `show_model(name)`        → Modelfile / parameters do modelo
A sonda empírica (Camada B) fica para `bauer doctor --deep`, ainda não implementada
nesta fase.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .http_shared import shared_ssl_context


class OllamaError(Exception):
    pass


@dataclass
class ModelfileParams:
    """Subset relevante dos parameters de um Modelfile."""

    num_ctx: int | None
    context_length: int | None  # da arquitetura nativa (model_info)
    size_bytes: int             # tamanho do arquivo no disco
    raw: dict[str, Any]


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434", timeout_seconds: int = 30, api_key: str = ""):
        self.host = host.rstrip("/")
        self.timeout = timeout_seconds
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )
        self.num_ctx: int | None = None   # definido pelo doctor (applied_context)
        self.think: bool | None = None   # None → False (desabilita thinking mode)

    # --- saude / disponibilidade ------------------------------------------------

    def is_alive(self) -> tuple[bool, str]:
        """Retorna (alive, motivo). motivo é vazio quando alive=True.

        Probe de liveness usa timeout curto (teto de 2s) independente do
        timeout de chat: um Ollama saudável responde /api/tags em milissegundos,
        e esperar o timeout cheio (host inalcançável/firewall drop) segurava o
        startup do `bauer agent` por vários segundos.
        """
        _probe_timeout = min(float(self.timeout), 2.0)
        try:
            r = httpx.get(f"{self.host}/api/tags", headers=self._headers, timeout=_probe_timeout, verify=shared_ssl_context())
            if r.status_code == 200:
                return True, ""
            return False, f"HTTP {r.status_code} de {self.host}/api/tags"
        except httpx.ConnectError as exc:
            return False, f"Conexao recusada em {self.host} ({exc})"
        except httpx.TimeoutException:
            return False, f"Timeout ({_probe_timeout:.0f}s) conectando em {self.host}"
        except httpx.HTTPError as exc:
            return False, f"Erro HTTP: {exc}"
        except Exception as exc:
            return False, f"Falha inesperada conectando em {self.host}: {exc}"

    # --- consulta de modelos ----------------------------------------------------

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.host}/api/tags", headers=self._headers, timeout=self.timeout, verify=shared_ssl_context())
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Falha ao listar modelos: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Falha inesperada ao listar modelos: {exc}") from exc
        data = r.json()
        return [m.get("name", "?") for m in data.get("models", [])]

    def list_models_with_sizes(self) -> list[dict]:
        """Retorna lista de {name, size_bytes} para todos os modelos instalados."""
        try:
            r = httpx.get(f"{self.host}/api/tags", headers=self._headers, timeout=self.timeout, verify=shared_ssl_context())
            r.raise_for_status()
            data = r.json()
            return [
                {"name": m.get("name", "?"), "size_bytes": m.get("size", 0)}
                for m in data.get("models", [])
            ]
        except Exception:
            return []

    def has_model(self, name: str) -> bool:
        return self.resolve_model_name(name) is not None

    def resolve_model_name(self, name: str) -> str | None:
        """Retorna o nome exato no Ollama que bate com 'name', ou None se não encontrado.

        Aceita match exato ou por prefixo de base+tag (ex: "gemma4:12b" resolve
        "gemma4:12b-it" se for o único modelo gemma4 instalado).
        """
        try:
            installed = self.list_models()
        except OllamaError:
            return None
        if name in installed:
            return name
        name_base = name.split(":")[0]
        name_tag = name.split(":")[1] if ":" in name else ""
        for m in installed:
            m_base = m.split(":")[0]
            m_tag = m.split(":")[1] if ":" in m else ""
            if m_base == name_base and (not name_tag or m_tag.startswith(name_tag) or name_tag.startswith(m_tag)):
                return m
        return None

    def show_model(self, name: str) -> ModelfileParams:
        """Retorna parameters do Modelfile (Decisao 2 - Camada A)."""
        try:
            r = httpx.post(
                f"{self.host}/api/show",
                json={"name": name},
                headers=self._headers,
                timeout=self.timeout,
                verify=shared_ssl_context(),
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise OllamaError(f"Modelo '{name}' nao encontrado no Ollama.") from exc
            raise OllamaError(f"Erro ao consultar modelo '{name}': {exc}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Falha em /api/show: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Falha inesperada em /api/show: {exc}") from exc

        data = r.json()
        params = data.get("parameters") or {}
        num_ctx = _extract_num_ctx(params)
        # Extrai context_length nativo da arquitetura (ex: gemma3.context_length)
        context_length: int | None = None
        for key, value in (data.get("model_info") or {}).items():
            if key.endswith(".context_length"):
                try:
                    context_length = int(value)
                except (TypeError, ValueError):
                    pass
                break
        # Tamanho do arquivo em bytes (disponível no /api/show via details.size ou tags)
        size_bytes = data.get("size_vram") or 0
        return ModelfileParams(num_ctx=num_ctx, context_length=context_length, size_bytes=size_bytes, raw=data)


    def chat_stream(self, model: str, messages: list[dict], num_ctx: int | None = None) -> Iterator[str]:
        """Streaming de resposta via /api/chat. Yields chunks de texto conforme chegam.

        Levanta OllamaError com mensagem clara em qualquer falha de rede ou HTTP.
        Premortem item 9: erro precisa ter causa, valor configurado e ação sugerida.
        """
        effective_num_ctx = num_ctx or self.num_ctx
        # `think` é top-level no /api/chat do Ollama (não dentro de options).
        # Desabilitar evita que gemma4 e similares retornem resposta no campo
        # `thinking` com `content` vazio — o que o parser interpretaria como vazio.
        # self.think=None → False (desabilitado por padrão); True ativa thinking mode.
        think_flag = self.think if self.think is not None else False
        body: dict = {"model": model, "messages": messages, "stream": True, "think": think_flag}
        if effective_num_ctx:
            body["options"] = {"num_ctx": effective_num_ctx}
        try:
            with httpx.stream(
                "POST",
                f"{self.host}/api/chat",
                json=body,
                headers=self._headers,
                timeout=httpx.Timeout(
                    connect=float(self.timeout),
                    read=300.0,   # 5 min para respostas longas
                    write=10.0,
                    pool=5.0,
                ),
                verify=shared_ssl_context(),
            ) as response:
                response.raise_for_status()
                thinking_buf: list[str] = []
                content_seen = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = data.get("message") or {}
                    if data.get("done"):
                        # Fallback: se o modelo só gerou thinking e nada de content,
                        # emite o thinking como resposta (comportamento seguro).
                        if not content_seen and thinking_buf:
                            yield "".join(thinking_buf)
                        break
                    chunk = msg.get("content", "")
                    if chunk:
                        content_seen = True
                        yield chunk
                    # Captura thinking como fallback caso content nunca apareça.
                    thinking = msg.get("thinking", "")
                    if thinking and not content_seen:
                        thinking_buf.append(thinking)
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Conexao recusada em {self.host}.\n"
                f"Verifique se o Ollama esta rodando: ollama serve"
            ) from exc
        except httpx.TimeoutException:
            raise OllamaError(
                f"Timeout ({self.timeout}s) em {self.host}.\n"
                f"O modelo pode estar sendo carregado — tente novamente em alguns segundos."
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise OllamaError(
                f"HTTP {status} em /api/chat para modelo '{model}'.\n"
                f"Verifique se o modelo esta disponivel: ollama list"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Erro em /api/chat: {exc}") from exc


def _extract_num_ctx(parameters: Any) -> int | None:
    """`parameters` no /api/show pode vir como string ou dict - extrai num_ctx."""
    if isinstance(parameters, dict):
        v = parameters.get("num_ctx")
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return None
    if isinstance(parameters, str):
        for line in parameters.splitlines():
            line = line.strip()
            if line.startswith("num_ctx"):
                parts = line.split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    return int(parts[-1])
    return None
