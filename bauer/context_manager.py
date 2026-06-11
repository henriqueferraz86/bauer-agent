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

# ─── Context windows por provider ──────────────────────────────────────────────
# FONTE ÚNICA: provider_profile.py (default_context de cada profile).
# Este alias existe porque testes e código antigo importam o nome daqui.
# Bug real (2026-06-10): este mapa divergia do mapa do preflight (opencode
# 128000 aqui vs 65536 lá) — consolidado na fonte única.
from .provider_profile import default_context_map as _default_context_map  # noqa: E402

PROVIDER_CONTEXT_WINDOWS: dict[str, int] = _default_context_map()

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
        # Tail dinâmico: bug real (2026-06-10) — com budget 3072 (ctx 4096) o tail
        # fixo de 8192 era maior que o budget inteiro → to_compress sempre vazio →
        # compressão jamais disparava e o modelo travava com contexto cheio.
        # Regra: tail = min(constante, 1/3 do budget), floor 512.
        self._tail_budget = min(TAIL_BUDGET_TOKENS, max(512, self._budget // 3))

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
        """Janela de contexto efetiva aplicada."""
        return self.applied_context or PROVIDER_CONTEXT_WINDOWS.get(self.provider, 32768)

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
        result.extend(_strip_orphan_tool_messages(self.messages))
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
        to_compress, tail = _split_tail_by_tokens(self.messages, self._tail_budget)
        if not to_compress:
            return  # nada a comprimir além do tail

        tokens_before = self.used_tokens

        # ── 2. Tool result pruning (sem LLM call) ──────────────────────────
        to_compress = _prune_tool_results(to_compress)

        # ── 3. Compressão semântica ou rule-based ──────────────────────────
        _client, _model = self._compression_client()
        if _client and _model:
            summary = _summarize_llm(_client, _model, to_compress)
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

    def _compression_client(self) -> tuple[Any, str]:
        """Seleciona o cliente de compressão: auxiliary PRIMEIRO, principal depois.

        O auxiliary (modelo leve/barato, configurável em `auxiliary.compression_model`)
        tem prioridade — comprimir histórico com o modelo principal da sessão
        desperdiça o modelo caro numa tarefa de resumo. Fallback: modelo da
        sessão; sem nenhum → (None, "") e o caller usa o rule-based.
        """
        try:
            from .auxiliary_client import get_compression_client as _get_aux
            _aux_client, _aux_model = _get_aux()
            if _aux_client and _aux_model:
                return _aux_client, _aux_model
        except Exception:
            pass
        if self._llm_client and self._llm_model:
            return self._llm_client, self._llm_model
        return None, ""

    def force_compress(self) -> bool:
        """Comprime o histórico AGORA, ignorando o threshold.

        Usado pelo auto-recovery quando o modelo retorna resposta vazia —
        contexto sobrecarregado é causa comum e comprimir + retry resolve
        sem intervenção do usuário. Retorna True se algo foi comprimido.
        """
        to_compress, tail = _split_tail_by_tokens(self.messages, self._tail_budget)
        if not to_compress:
            return False
        to_compress = _prune_tool_results(to_compress)
        _client, _model = self._compression_client()
        if _client and _model:
            summary = _summarize_llm(_client, _model, to_compress)
        else:
            summary = _summarize_messages(to_compress)
        self.messages = [
            {"role": "system", "content": f"[Resumo de contexto anterior]\n{summary}"}
        ] + tail
        self._compress_count += 1
        return True

    def _trim(self) -> None:
        """Remove mensagens antigas do início até caber no budget.

        Nunca remove a última mensagem (a do usuário recém-chegada) para
        garantir que sempre enviamos algo ao modelo.
        Remove pares assistant+tool_calls atomicamente para não deixar
        role:tool órfãos que causam 400 "tool_call_id is not set".
        """
        while len(self.messages) > 1 and _estimate_tokens(self.messages) > self._budget:
            dropped = self.messages.pop(0)
            # Se removemos um assistant com tool_calls, também remove os
            # tool results correspondentes para não deixar pares quebrados.
            if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
                tc_ids = {tc.get("id") for tc in (dropped["tool_calls"] or []) if tc.get("id")}
                if tc_ids:
                    self.messages = [
                        m for m in self.messages
                        if not (m.get("role") == "tool" and m.get("tool_call_id") in tc_ids)
                    ]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _strip_orphan_tool_messages(messages: list[dict]) -> list[dict]:
    """Remove pares assistant+tool quebrados que causariam 400 no provider.

    _trim e _auto_summarize podem remover a metade de um par:
    - remove o assistant com tool_calls mas deixa o role:tool atrás
    - remove o role:tool mas deixa o assistant com tool_calls na frente

    Providers como OpenCode/Xiaomi retornam "tool_call_id is not set" quando
    recebem role:tool sem um assistant:tool_calls correspondente.
    """
    declared_ids: set[str] = set()
    result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                if tc.get("id"):
                    declared_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            if msg.get("tool_call_id"):
                result_ids.add(msg["tool_call_id"])

    clean: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            if msg.get("tool_call_id") not in declared_ids:
                continue  # tool result sem assistant correspondente
        elif role == "assistant" and msg.get("tool_calls"):
            tc_ids = {tc.get("id") for tc in (msg.get("tool_calls") or []) if tc.get("id")}
            if tc_ids and not any(tid in result_ids for tid in tc_ids):
                continue  # assistant com tool_calls sem nenhum resultado correspondente
        clean.append(msg)
    return clean


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
        f"{m['role'].upper()}: {(m.get('content') or '')[:400]}"
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
