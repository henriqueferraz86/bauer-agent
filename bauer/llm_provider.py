"""MODEL-001 — Abstração LLMProvider.

Define a interface base que todos os clientes LLM do Bauer devem implementar.
Garante que o agente pode trocar de provider (Ollama, OpenAI, Anthropic, Groq...)
sem alterar o código de orquestração.

Interface mínima obrigatória:
  - generate(messages, model, **kwargs) → str
  - stream(messages, model, **kwargs) → Iterator[str]

Interface estendida opcional (detectada por hasattr):
  - tool_call(messages, tools, model, **kwargs) → dict
  - embed(text, model, **kwargs) → list[float]
  - classify(text, labels, model, **kwargs) → str
"""

from __future__ import annotations

import abc
from typing import Any, Generator, Iterator


class LLMProvider(abc.ABC):
    """Classe base abstrata para todos os provedores LLM do Bauer.

    Subclasses devem implementar pelo menos `generate` e `stream`.
    Métodos opcionais (`tool_call`, `embed`, `classify`) lançam
    NotImplementedError por padrão — o chamador deve verificar
    `provider.supports("tool_call")` antes de usar.

    Atributos esperados pelas subclasses:
        model_name (str): modelo padrão
        base_url (str | None): endpoint base
        api_key (str | None): chave de API
        timeout (int): timeout em segundos
    """

    # ── Interface obrigatória ─────────────────────────────────────────────────

    @abc.abstractmethod
    def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Gera resposta completa (non-streaming).

        Args:
            messages: Lista de mensagens no formato OpenAI (role + content).
            model: Override do modelo padrão. Se None, usa self.model_name.
            temperature: Temperatura de amostragem (0.0 = determinístico).
            max_tokens: Limite máximo de tokens na resposta.
            **kwargs: Parâmetros extras específicos do provider.

        Returns:
            Texto gerado como string.

        Raises:
            LLMError: Em caso de falha de API ou timeout.
        """

    @abc.abstractmethod
    def stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Gera resposta em streaming (yields chunks de texto).

        Args:
            messages: Lista de mensagens no formato OpenAI.
            model: Override do modelo padrão.
            temperature: Temperatura de amostragem.
            max_tokens: Limite de tokens.
            **kwargs: Parâmetros extras.

        Yields:
            Chunks de texto à medida que são recebidos.

        Raises:
            LLMError: Em caso de falha.
        """

    # ── Interface estendida (opcional) ────────────────────────────────────────

    def tool_call(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """Executa uma chamada com native tool calling (function calling).

        Args:
            messages: Histórico de mensagens.
            tools: Lista de schemas de tools (formato OpenAI).
            model: Override do modelo.
            **kwargs: Parâmetros extras.

        Returns:
            Dict com chaves: {"tool_name": str, "tool_args": dict, "raw": ...}

        Raises:
            NotImplementedError: Se o provider não suporta tool calling.
            LLMError: Em caso de falha de API.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} não implementa tool_call(). "
            "Use um provider com suporte a native function calling."
        )

    def embed(
        self,
        text: str | list[str],
        model: str | None = None,
        **kwargs: Any,
    ) -> list[float] | list[list[float]]:
        """Gera embeddings vetoriais para texto.

        Args:
            text: Texto ou lista de textos para embeddar.
            model: Modelo de embedding. Se None, usa modelo padrão.
            **kwargs: Parâmetros extras.

        Returns:
            Lista de floats (single) ou lista de listas (batch).

        Raises:
            NotImplementedError: Se o provider não suporta embeddings.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} não implementa embed(). "
            "Use um provider com suporte a embeddings (ex: OpenAI, Ollama)."
        )

    def classify(
        self,
        text: str,
        labels: list[str],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Classifica texto em uma das labels fornecidas.

        Implementação padrão usa generate() com prompt zero-shot.
        Providers podem sobrescrever com classificação nativa.

        Args:
            text: Texto a classificar.
            labels: Labels possíveis.
            model: Override do modelo.

        Returns:
            Uma das labels fornecidas.
        """
        if not labels:
            raise ValueError("classify: 'labels' não pode ser vazio.")
        prompt = (
            f"Classifique o texto abaixo em uma das categorias: {', '.join(labels)}.\n"
            f"Responda APENAS com o nome exato da categoria, sem explicação.\n\n"
            f"Texto: {text}\n\nCategoria:"
        )
        response = self.generate(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            **kwargs,
        ).strip()
        # Tenta match exato
        for label in labels:
            if response.lower() == label.lower():
                return label
        # Fallback: retorna a label que aparece na resposta
        for label in labels:
            if label.lower() in response.lower():
                return label
        return labels[0]  # último recurso

    # ── Utilitários ───────────────────────────────────────────────────────────

    def supports(self, capability: str) -> bool:
        """Verifica se o provider suporta uma capability opcional.

        Args:
            capability: "tool_call" | "embed" | "classify" | "stream"

        Returns:
            True se o provider tem implementação própria (não a default que lança NotImplementedError).
        """
        _ABSTRACT_DEFAULTS = {"tool_call", "embed"}
        if capability == "stream":
            return True  # obrigatório
        if capability == "generate":
            return True  # obrigatório
        if capability not in _ABSTRACT_DEFAULTS:
            # classify tem implementação default via generate
            return hasattr(self, capability) and callable(getattr(self, capability))
        # Verifica se a subclasse sobrescreveu o método
        cls_method = getattr(type(self), capability, None)
        base_method = getattr(LLMProvider, capability, None)
        return cls_method is not base_method

    @property
    def provider_name(self) -> str:
        """Nome legível do provider (ex: 'ollama', 'openai', 'anthropic')."""
        return self.__class__.__name__.replace("Client", "").lower()

    @property
    def model_name(self) -> str:
        """Modelo padrão do provider. Subclasses devem sobrescrever."""
        return getattr(self, "_model_name", "unknown")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model_name!r})"


class LLMError(Exception):
    """Erro de chamada ao LLM — wrapper para erros de API, timeout, etc."""

    def __init__(self, message: str, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.provider:
            parts.insert(0, f"[{self.provider}]")
        if self.status_code:
            parts.append(f"(HTTP {self.status_code})")
        return " ".join(parts)


class LLMProviderMixin:
    """Mixin para adaptar clientes existentes ao protocolo LLMProvider.

    Permite que clientes legados (OllamaClient, OpenAICompatClient, etc.)
    sejam compatíveis com LLMProvider sem reescrever toda a classe.

    Uso:
        class OllamaClient(LLMProviderMixin, existing_base):
            ...
    """

    def supports(self, capability: str) -> bool:
        """Verifica capabilities com base em métodos disponíveis."""
        if capability in ("generate", "stream"):
            return True
        method = getattr(self, capability, None)
        if method is None:
            return False
        # Verifica se não é o default do LLMProvider
        base = getattr(LLMProvider, capability, None)
        return base is None or (callable(method) and type(self).__dict__.get(capability) is not None)

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__.replace("Client", "").lower()


def is_llm_provider(obj: Any) -> bool:
    """Verifica se um objeto é compatível com LLMProvider (duck typing)."""
    return callable(getattr(obj, "generate", None)) and callable(getattr(obj, "stream", None))
