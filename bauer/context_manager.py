"""Gerenciador de histórico de conversa com corte conservador de contexto.

Fase 2: histórico em memória apenas — sem persistência (isso é Fase 3).
Garante que o histórico nunca ultrapasse o budget derivado do applied_context
do .runtime_state.json, reservando 25% para o output do modelo.

Suporta:
- Context budget por provider (mapeia janelas reais de contexto)
- Estimativa de tokens via heurística (chars/4)
- Compressão semântica via LLM quando disponível, rule-based como fallback
- Sliding window: mantém N mensagens recentes + resumo do restante
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHARS_PER_TOKEN = 4  # estimativa conservadora: 4 chars ≈ 1 token
SUMMARY_THRESHOLD_RATIO = 0.70  # comprime quando uso > 70% do budget
# Alias para compatibilidade com testes antigos
SUMMARY_THRESHOLD_TOKENS = int(32768 * SUMMARY_THRESHOLD_RATIO)  # ~22937 tokens

# ─── Context windows reais por provider ────────────────────────────────────────
# Valores em tokens. Usados quando applied_context=0 ou para validar o valor.
PROVIDER_CONTEXT_WINDOWS: dict[str, int] = {
    # Ollama (depende do modelo — usamos applied_context do preflight)
    "ollama": 32768,
    # OpenAI
    "openai": 128000,
    "openai-api": 128000,
    # Anthropic
    "anthropic": 200000,
    # Groq
    "groq": 32768,
    # Mistral
    "mistral": 128000,
    # xAI Grok
    "xai": 131072,
    # Together AI
    "together": 32768,
    # DeepSeek
    "deepseek": 65536,
    # Google Gemini 2.0
    "gemini": 1048576,
    # OpenRouter (depende do modelo; usamos um valor conservador)
    "openrouter": 128000,
    # Azure (depende do deployment)
    "azure": 128000,
    # GitHub Models (GPT-4o base)
    "github": 128000,
    # GitHub Copilot (GPT-4o / Claude Sonnet)
    "copilot": 128000,
    # OpenCode
    "opencode": 128000,
    # Custom fallback
    "custom": 32768,
}

KEEP_TAIL_MESSAGES = 6  # mensagens recentes sempre preservadas (3 turnos completos)


@dataclass
class ContextManager:
    applied_context: int
    system_prompt: str | None = None
    messages: list[dict] = field(default_factory=list)
    provider: str = "ollama"
    # Cliente LLM para compressão semântica (opcional; fallback rule-based se None)
    _llm_client: Any = field(default=None, repr=False)
    _llm_model: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        # Determina budget real:
        # 1. Se applied_context foi calibrado pelo preflight, usa ele
        # 2. Se não, usa o mapa de providers
        # 3. Reserva 25% para output do modelo; floor de 512
        effective = self.applied_context or PROVIDER_CONTEXT_WINDOWS.get(self.provider, 32768)
        self._budget = max(512, int(effective * 0.75))

    def set_llm(self, client: Any, model: str) -> None:
        """Configura cliente LLM para compressão semântica."""
        self._llm_client = client
        self._llm_model = model

    @property
    def budget(self) -> int:
        return self._budget

    @property
    def used_tokens(self) -> int:
        return _estimate_tokens(self.messages)

    @property
    def usage_pct(self) -> float:
        """Percentual de uso do budget (0.0 – 1.0+)."""
        if self._budget == 0:
            return 0.0
        return self.used_tokens / self._budget

    @property
    def context_window(self) -> int:
        """Janela de contexto real do provider."""
        return PROVIDER_CONTEXT_WINDOWS.get(self.provider, self.applied_context or 32768)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._auto_summarize()
        self._trim()

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def get_payload(self) -> list[dict]:
        """Retorna lista de mensagens pronta para o /api/chat."""
        result: list[dict] = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend(self.messages)
        return result

    def clear(self) -> None:
        self.messages.clear()

    def _auto_summarize(self) -> None:
        """Comprime mensagens antigas quando histórico ultrapassa 70% do budget.

        Se cliente LLM disponível: compressão semântica (pede ao modelo para resumir).
        Caso contrário: compressão rule-based (extração de tópicos/tools).
        Mantém KEEP_TAIL_MESSAGES mensagens recentes intactas (sliding window).
        """
        if self.usage_pct < SUMMARY_THRESHOLD_RATIO:
            return
        if len(self.messages) <= KEEP_TAIL_MESSAGES:
            return

        to_compress = self.messages[:-KEEP_TAIL_MESSAGES]
        tail = self.messages[-KEEP_TAIL_MESSAGES:]

        if self._llm_client and self._llm_model:
            summary = _summarize_llm(self._llm_client, self._llm_model, to_compress)
        else:
            summary = _summarize_messages(to_compress)

        self.messages = [
            {"role": "system", "content": f"[Resumo de contexto anterior]\n{summary}"}
        ] + tail

    def _trim(self) -> None:
        """Remove mensagens antigas do início até caber no budget.

        Nunca remove a última mensagem (a do usuário recém-chegada) para
        garantir que sempre enviamos algo ao modelo.
        """
        while len(self.messages) > 1 and _estimate_tokens(self.messages) > self._budget:
            self.messages.pop(0)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _estimate_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return total_chars // CHARS_PER_TOKEN


def _summarize_llm(client: Any, model: str, messages: list[dict]) -> str:
    """Compressão semântica via LLM — pede ao modelo para resumir o histórico."""
    transcript = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')[:400]}"
        for m in messages
    )
    prompt = (
        "Resuma o histórico de conversa abaixo em no máximo 300 palavras. "
        "Preserve: decisões tomadas, arquivos modificados, tools usadas, erros encontrados, "
        "e contexto necessário para continuar a tarefa. Seja objetivo.\n\n"
        f"HISTÓRICO:\n{transcript}"
    )
    try:
        parts: list[str] = []
        for chunk in client.chat_stream(model, [{"role": "user", "content": prompt}]):
            parts.append(chunk)
        return "".join(parts).strip() or _summarize_messages(messages)
    except Exception:
        return _summarize_messages(messages)


def _summarize_messages(messages: list[dict]) -> str:
    """Gera resumo textual de mensagens sem chamar o LLM (fallback rule-based).

    Extrai: quantidade de turnos, tópicos mencionados (palavras longas frequentes),
    tools chamadas, e últimas decisões visíveis.
    """
    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    assistant_msgs = [m["content"] for m in messages if m.get("role") == "assistant"]

    # Conta tools usadas
    import re
    tools_used: list[str] = []
    for msg in assistant_msgs:
        match = re.search(r'"action"\s*:\s*"(\w+)"', msg)
        if match:
            tools_used.append(match.group(1))

    # Palavras-chave relevantes do usuário (palavras longas, ignorando stopwords)
    stopwords = {"para", "como", "que", "uma", "não", "mais", "com", "por", "está", "isso", "você"}
    word_counter: dict[str, int] = {}
    for msg in user_msgs:
        for word in re.findall(r"\b[a-zA-ZÀ-ú]{5,}\b", msg.lower()):
            if word not in stopwords:
                word_counter[word] = word_counter.get(word, 0) + 1
    top_words = sorted(word_counter, key=lambda w: -word_counter[w])[:8]

    lines = [
        f"Turnos comprimidos: {len(user_msgs)} perguntas do usuário.",
    ]
    if top_words:
        lines.append(f"Tópicos principais: {', '.join(top_words)}.")
    if tools_used:
        unique_tools = list(dict.fromkeys(tools_used))
        lines.append(f"Tools usadas: {', '.join(unique_tools)}.")
    if assistant_msgs:
        last = assistant_msgs[-1][:200].replace("\n", " ")
        lines.append(f"Última resposta do assistente (resumida): {last}...")

    return "\n".join(lines)
