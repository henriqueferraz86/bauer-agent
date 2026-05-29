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


class OllamaError(Exception):
    pass


@dataclass
class ModelfileParams:
    """Subset relevante dos parameters de um Modelfile."""

    num_ctx: int | None
    raw: dict[str, Any]


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434", timeout_seconds: int = 30, api_key: str = ""):
        self.host = host.rstrip("/")
        self.timeout = timeout_seconds
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )

    # --- saude / disponibilidade ------------------------------------------------

    def is_alive(self) -> tuple[bool, str]:
        """Retorna (alive, motivo). motivo é vazio quando alive=True."""
        try:
            r = httpx.get(f"{self.host}/api/tags", headers=self._headers, timeout=self.timeout)
            if r.status_code == 200:
                return True, ""
            return False, f"HTTP {r.status_code} de {self.host}/api/tags"
        except httpx.ConnectError as exc:
            return False, f"Conexao recusada em {self.host} ({exc})"
        except httpx.TimeoutException:
            return False, f"Timeout ({self.timeout}s) conectando em {self.host}"
        except httpx.HTTPError as exc:
            return False, f"Erro HTTP: {exc}"
        except Exception as exc:
            return False, f"Falha inesperada conectando em {self.host}: {exc}"

    # --- consulta de modelos ----------------------------------------------------

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.host}/api/tags", headers=self._headers, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Falha ao listar modelos: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Falha inesperada ao listar modelos: {exc}") from exc
        data = r.json()
        return [m.get("name", "?") for m in data.get("models", [])]

    def has_model(self, name: str) -> bool:
        try:
            return name in self.list_models()
        except OllamaError:
            return False

    def show_model(self, name: str) -> ModelfileParams:
        """Retorna parameters do Modelfile (Decisao 2 - Camada A)."""
        try:
            r = httpx.post(
                f"{self.host}/api/show",
                json={"name": name},
                headers=self._headers,
                timeout=self.timeout,
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
        return ModelfileParams(num_ctx=num_ctx, raw=data)


    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        """Streaming de resposta via /api/chat. Yields chunks de texto conforme chegam.

        Levanta OllamaError com mensagem clara em qualquer falha de rede ou HTTP.
        Premortem item 9: erro precisa ter causa, valor configurado e ação sugerida.
        """
        try:
            with httpx.stream(
                "POST",
                f"{self.host}/api/chat",
                json={"model": model, "messages": messages, "stream": True},
                headers=self._headers,
                timeout=httpx.Timeout(
                    connect=float(self.timeout),
                    read=300.0,   # 5 min para respostas longas
                    write=10.0,
                    pool=5.0,
                ),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("done"):
                        break
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
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
