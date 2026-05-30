"""Gerenciador de histórico de conversa com corte conservador de contexto.

Fase 2: histórico em memória apenas — sem persistência (isso é Fase 3).
Garante que o histórico nunca ultrapasse o budget derivado do applied_context
do .runtime_state.json, reservando 25% para o output do modelo.

Suporta:
- Context budget por provider (mapeia janelas reais de contexto)
- Estimativa de tokens via heurística (chars/4)
- Compressão semântica via LLM quando disponível, rule-based como fallback
- Tail protection dinâmica por tokens (não contagem fixa de mensagens)
- Anti-thrashing: evita compressões inúteis que economizam < 10%
- Tool result pruning: simplifica resultados duplicados/longos antes de comprimir
"""

from __future__ import annotations

import re
import time
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

# ─── Tail protection dinâmica por tokens ──────────────────────────────────────
# Mantém as mensagens mais recentes que cabem neste budget.
# ~8 K tokens = ~32 KB de texto ≈ 3–5 turnos completos com respostas médias.
# Escala melhor que contagem fixa: não desperdiça com modelos 128 K nem corta
# demais em modelos 4 K.
TAIL_BUDGET_TOKENS = 8192

# Legado — mantido para compatibilidade com testes antigos
KEEP_TAIL_MESSAGES = 6

# ─── Anti-thrashing ────────────────────────────────────────────────────────────
# Se uma compressão economizou menos de THRASH_MIN_SAVINGS do contexto,
# aumentamos o threshold temporariamente para evitar recompressões inúteis.
THRASH_MIN_SAVINGS = 0.10      # 10 % mínimo de economia para considerar "útil"
THRASH_BOOST_STEP  = 0.10      # quanto sobe o threshold a cada compressão ruim
THRASH_BOOST_MAX   = 0.20      # teto do boost (threshold máximo = 0.70 + 0.20 = 0.90)
THRASH_DECAY_STEP  = 0.05      # quanto cai o boost quando compressão é boa

# ─── Tool result pruning ───────────────────────────────────────────────────────
# Limite de chars para resultado de tool no bloco de compressão.
# Resultados mais longos viram 1 linha de resumo.
PRUNE_RESULT_MAX_CHARS = 400


@dataclass
class ContextManager:
    applied_context: int
    system_prompt: str | None = None
    messages: list[dict] = field(default_factory=list)
    provider: str = "ollama"
    # Cliente LLM para compressão semântica (opcional; fallback rule-based se None)
    _llm_client: Any = field(default=None, repr=False)
    _llm_model: str = field(default="", repr=False)

    # ── Anti-thrashing state ────────────────────────────────────────────────
    _threshold_boost: float = field(default=0.0, repr=False)
    _compress_count: int = field(default=0, repr=False)
    _last_savings_pct: float = field(default=1.0, repr=False)

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

    @property
    def effective_threshold(self) -> float:
        """Threshold atual, incluindo boost anti-thrashing."""
        return min(SUMMARY_THRESHOLD_RATIO + self._threshold_boost, 0.95)

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

    def compression_stats(self) -> dict:
        """Retorna estatísticas de compressão para diagnóstico/logs."""
        return {
            "compress_count": self._compress_count,
            "threshold_boost": round(self._threshold_boost, 3),
            "effective_threshold": round(self.effective_threshold, 3),
            "last_savings_pct": round(self._last_savings_pct, 3),
            "used_tokens": self.used_tokens,
            "budget": self._budget,
            "usage_pct": round(self.usage_pct, 3),
        }

    def _auto_summarize(self) -> None:
        """Comprime mensagens antigas quando histórico ultrapassa o threshold efetivo.

        Melhorias vs. implementação original:
        1. Tail dinâmico por tokens (TAIL_BUDGET_TOKENS) — não contagem fixa.
        2. Tool result pruning antes do LLM — remove duplicados e trunca longos.
        3. Anti-thrashing — se compressão economizou < 10%, sobe o threshold
           temporariamente para evitar recompressões imediatas inúteis.
        """
        if self.usage_pct < self.effective_threshold:
            return

        # ── 1. Split tail dinâmico por tokens ──────────────────────────────
        to_compress, tail = _split_tail_by_tokens(self.messages, TAIL_BUDGET_TOKENS)
        if not to_compress:
            return  # nada a comprimir além do tail

        tokens_before = self.used_tokens

        # ── 2. Tool result pruning (sem LLM call) ──────────────────────────
        to_compress = _prune_tool_results(to_compress)

        # ── 3. Compressão semântica ou rule-based ──────────────────────────
        if self._llm_client and self._llm_model:
            summary = _summarize_llm(self._llm_client, self._llm_model, to_compress)
        else:
            summary = _summarize_messages(to_compress)

        self.messages = [
            {"role": "system", "content": f"[Resumo de contexto anterior]\n{summary}"}
        ] + tail

        # ── 4. Anti-thrashing ──────────────────────────────────────────────
        tokens_after = self.used_tokens
        self._compress_count += 1

        if tokens_before > 0:
            savings = (tokens_before - tokens_after) / tokens_before
            self._last_savings_pct = savings

            if savings < THRASH_MIN_SAVINGS:
                # Compressão inútil → sobe threshold para evitar repetição imediata
                self._threshold_boost = min(
                    self._threshold_boost + THRASH_BOOST_STEP, THRASH_BOOST_MAX
                )
            else:
                # Boa compressão → relaxa boost gradualmente
                self._threshold_boost = max(
                    self._threshold_boost - THRASH_DECAY_STEP, 0.0
                )

    def _trim(self) -> None:
        """Remove mensagens antigas do início até caber no budget.

        Nunca remove a última mensagem (a do usuário recém-chegada) para
        garantir que sempre enviamos algo ao modelo.
        """
        while len(self.messages) > 1 and _estimate_tokens(self.messages) > self._budget:
            self.messages.pop(0)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _estimate_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    return total_chars // CHARS_PER_TOKEN


def _split_tail_by_tokens(
    messages: list[dict], tail_budget: int
) -> tuple[list[dict], list[dict]]:
    """Divide messages em (to_compress, tail) preservando mensagens recentes.

    O tail contém as mensagens mais recentes que cabem em tail_budget tokens.
    Garante que pelo menos 1 mensagem fica no tail (nunca retorna tail vazio
    se messages não for vazio).

    Returns:
        (to_compress, tail) — to_compress pode ser [] se tudo cabe no tail.
    """
    if not messages:
        return [], []

    tail: list[dict] = []
    used = 0

    for msg in reversed(messages):
        cost = _estimate_tokens([msg])
        # Sempre aceita pelo menos 1 mensagem no tail
        if tail and used + cost > tail_budget:
            break
        tail.insert(0, msg)
        used += cost

    to_compress = messages[: len(messages) - len(tail)]
    return to_compress, tail


_TOOL_RESULT_RE = re.compile(r"^\[Resultado de (\w+)\]", re.MULTILINE)


def _prune_tool_results(messages: list[dict]) -> list[dict]:
    """Simplifica resultados de tool antes da compressão LLM (sem chamada extra).

    Aplica duas otimizações:
    1. Deduplicação: mesmo action + primeiros 80 chars de resultado idênticos
       → comprime repetições em 1 linha.
    2. Truncagem: resultados > PRUNE_RESULT_MAX_CHARS → substitui por 1 linha
       com action + contagem de linhas.

    Só altera mensagens de role 'user' que contêm o padrão
    '[Resultado de {action}]' — formato injetado pelo agent.py.
    """
    pruned: list[dict] = []
    seen: dict[str, int] = {}  # fingerprint → primeira posição vista

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role != "user":
            pruned.append(msg)
            continue

        # Detecta blocos de resultados de tool
        tool_matches = list(_TOOL_RESULT_RE.finditer(content))
        if not tool_matches:
            pruned.append(msg)
            continue

        # Mensagem pode ter múltiplos blocos (combined_parts do agent.py)
        new_parts: list[str] = []
        blocks = re.split(r"\n\n(?=\[Resultado de )", content)

        for block in blocks:
            m = _TOOL_RESULT_RE.match(block)
            if not m:
                new_parts.append(block)
                continue

            action = m.group(1)
            result_body = block[m.end():].strip()
            fingerprint = f"{action}:{result_body[:80]}"
            lines = result_body.splitlines()
            n_lines = len(lines)
            n_chars = len(result_body)

            if fingerprint in seen:
                # Duplicado — resume em 1 linha
                seen[fingerprint] += 1
                new_parts.append(
                    f"[Resultado de {action}] (duplicado #{seen[fingerprint]}, omitido)"
                )
            elif n_chars > PRUNE_RESULT_MAX_CHARS:
                # Muito longo — 1 linha de sumário
                first_line = lines[0][:120] if lines else result_body[:120]
                new_parts.append(
                    f"[Resultado de {action}] {first_line} "
                    f"[...{n_lines} linhas / {n_chars} chars — truncado na compressão]"
                )
                seen[fingerprint] = 1
            else:
                new_parts.append(block)
                seen[fingerprint] = 1

        new_content = "\n\n".join(new_parts)
        pruned.append({**msg, "content": new_content})

    return pruned


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
    tools_used: list[str] = []
    for msg in assistant_msgs:
        match = re.search(r'"action"\s*:\s*"(\w+)"', msg)
        if match:
            tools_used.append(match.group(1))

    # Tools nos resultados injetados
    for msg in user_msgs:
        for m in _TOOL_RESULT_RE.finditer(msg):
            tools_used.append(m.group(1))

    # Palavras-chave relevantes do usuário (palavras longas, ignorando stopwords)
    stopwords = {"para", "como", "que", "uma", "não", "mais", "com", "por", "está", "isso", "você"}
    word_counter: dict[str, int] = {}
    for msg in user_msgs:
        # Ignora linhas de resultado de tool
        clean = re.sub(r"\[Resultado de \w+\].*", "", msg, flags=re.DOTALL)
        for word in re.findall(r"\b[a-zA-ZÀ-ú]{5,}\b", clean.lower()):
            if word not in stopwords:
                word_counter[word] = word_counter.get(word, 0) + 1
    top_words = sorted(word_counter, key=lambda w: -word_counter[w])[:8]

    lines = [
        f"Turnos comprimidos: {len(user_msgs)} mensagens do usuário.",
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
