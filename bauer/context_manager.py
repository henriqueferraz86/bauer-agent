"""Gerenciador de histórico de conversa com corte conservador de contexto.

Suporta:
- Context budget por provider (mapeia janelas reais de contexto)
- Estimativa de tokens via heurística (chars/4)
- Compressão semântica via LLM quando disponível, rule-based como fallback
- Template estruturado de compressão com SUMMARY_PREFIX (handoff claro)
- Updates iterativos: segunda compressão atualiza o sumário anterior
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

# ─── Context compaction handoff ────────────────────────────────────────────────
# Prefixo injetado no sumário para que o modelo não trate o sumário como
# instruções ativas — pattern portado do Hermes/KiloCode.
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERÊNCIA APENAS] Turnos anteriores foram compactados "
    "no sumário abaixo. Trate como contexto de fundo, NÃO como instruções ativas. "
    "Responda APENAS à última mensagem do usuário que aparece APÓS este sumário. "
    "Se a última mensagem contradiz ou muda de assunto em relação ao '## Tarefa Ativa', "
    "a mensagem mais recente PREVALECE — descarte itens obsoletos e não os retome. "
    "Sua memória persistente (MEMORY.md, USER.md) no system prompt é SEMPRE autoritativa. "
    "O estado atual do sistema (arquivos, config) pode refletir o trabalho descrito aqui:"
)

# Token budget mínimo para o sumário gerado pelo LLM
_MIN_SUMMARY_TOKENS = 1500
# Proporção do conteúdo comprimido alocada ao sumário
_SUMMARY_RATIO = 0.20
# Teto absoluto em tokens para o sumário
_SUMMARY_TOKENS_CEILING = 8000
# Chars por token (estimativa conservadora)
_CHARS_PER_TOKEN = 4


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

    # ── Iterative summary state ─────────────────────────────────────────────
    _previous_summary: str | None = field(default=None, repr=False)
    _summary_failure_cooldown_until: float = field(default=0.0, repr=False)

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

    def shrink_budget(self, provider_cap_tokens: int) -> bool:
        """Reduz o budget quando o provider reporta uma janela REAL menor.

        Caso real (2026-07-02): applied_context=128000 (nominal do modelo),
        mas o endpoint free do OpenRouter corta em 65536 — o histórico crescia
        até ~66k sem nunca atingir o threshold de compressão (70% de 96k) e
        TODA chamada passava a falhar com 400. Ao parsear o cap do erro,
        encolhemos o budget para o valor real e a compressão volta a disparar.

        Retorna True se o budget foi de fato reduzido.
        """
        if provider_cap_tokens <= 0:
            return False
        new_budget = max(512, int(provider_cap_tokens * 0.75))
        if new_budget >= self._budget:
            return False  # cap reportado não é menor que o budget atual
        self.applied_context = provider_cap_tokens
        self._budget = new_budget
        self._tail_budget = min(TAIL_BUDGET_TOKENS, max(512, self._budget // 3))
        return True

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

    def add_ephemeral_system(self, text: str) -> None:
        """Inject a temporary system note before the next user turn.

        Unlike the permanent system_prompt, ephemeral notes are inserted as a
        ``role=system`` message in the messages list and will be compressed or
        trimmed as the context grows.  Used for per-turn memory context injection.
        """
        self.messages.append({"role": "system", "content": text})

    def get_payload(self) -> list[dict]:
        """Retorna lista de mensagens pronta para o /api/chat.

        Sana pares assistant+tool quebrados in-place: ctx.messages é atualizado
        para que o estado corrigido seja o que persiste no SQLite após o turno.
        """
        self.messages = _strip_orphan_tool_messages(self.messages)
        result: list[dict] = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend(self.messages)
        return result

    def clear(self) -> None:
        self.messages.clear()
        self._previous_summary = None
        self._summary_failure_cooldown_until = 0.0

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
            "has_previous_summary": self._previous_summary is not None,
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
        now = time.monotonic()
        if now < self._summary_failure_cooldown_until:
            # Em cooldown após falha — usa rule-based para não bloquear
            summary_text = _summarize_messages(to_compress)
            self._previous_summary = None
        else:
            _client, _model = self._compression_client()
            if _client and _model:
                summary_text, ok = _summarize_llm_structured(
                    _client, _model, to_compress,
                    previous_summary=self._previous_summary,
                )
                if ok:
                    self._previous_summary = summary_text
                    self._summary_failure_cooldown_until = 0.0
                else:
                    # LLM falhou — cooldown 60s, usa rule-based
                    self._summary_failure_cooldown_until = time.monotonic() + 60.0
                    if not summary_text:
                        summary_text = _summarize_messages(to_compress)
            else:
                summary_text = _summarize_messages(to_compress)

        # Wrap com SUMMARY_PREFIX para o modelo não tratar como instruções
        compaction_content = f"{SUMMARY_PREFIX}\n{summary_text}"
        self.messages = [
            {"role": "system", "content": compaction_content}
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
            summary_text, ok = _summarize_llm_structured(
                _client, _model, to_compress,
                previous_summary=self._previous_summary,
            )
            if ok:
                self._previous_summary = summary_text
            elif not summary_text:
                summary_text = _summarize_messages(to_compress)
        else:
            summary_text = _summarize_messages(to_compress)
        compaction_content = f"{SUMMARY_PREFIX}\n{summary_text}"
        self.messages = [
            {"role": "system", "content": compaction_content}
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

    Dois tipos de quebra:
    1. role:tool sem assistant:tool_calls correspondente (tool result órfão)
    2. role:assistant com tool_calls onde nenhuma call tem id válido, ou não
       tem result correspondente (assistant:tool_calls malformado/incompleto)

    Providers como OpenCode/Xiaomi retornam "tool_call_id is not set" para
    qualquer das duas situações.

    get_payload() chama esta função E também atualiza ctx.messages em lugar
    (self.messages = clean) para que o estado quebrado não persista no SQLite.
    """
    declared_ids: set[str] = set()
    result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                if tc.get("id"):
                    declared_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                result_ids.add(tc_id)

    clean: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            tc_id = msg.get("tool_call_id")
            if not tc_id or tc_id not in declared_ids:
                continue  # sem tool_call_id ou sem assistant correspondente
        elif role == "assistant" and msg.get("tool_calls"):
            calls = msg.get("tool_calls") or []
            # Mantém só as calls que têm result correspondente — descarta as
            # penduradas (caso de truncamento parcial de batch). Assim um único
            # tool_call sem resposta não invalida a mensagem inteira nem o request.
            answered = [tc for tc in calls if tc.get("id") and tc["id"] in result_ids]
            if not answered:
                continue  # nenhuma call respondida → remove a mensagem
            if len(answered) != len(calls):
                msg = {**msg, "tool_calls": answered}  # filtra as órfãs
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


def _serialize_for_summary(messages: list[dict]) -> str:
    """Serializa mensagens para o summarizer incluindo tool calls/results."""
    _CONTENT_MAX = 3000
    _CONTENT_HEAD = 2000
    _CONTENT_TAIL = 800
    _TOOL_ARGS_MAX = 800

    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")
            )

        if role == "tool":
            tool_id = msg.get("tool_call_id", "")
            if len(content) > _CONTENT_MAX:
                content = content[:_CONTENT_HEAD] + "\n...[truncado]...\n" + content[-_CONTENT_TAIL:]
            parts.append(f"[TOOL RESULT {tool_id}]: {content}")
            continue

        if role == "assistant":
            if len(content) > _CONTENT_MAX:
                content = content[:_CONTENT_HEAD] + "\n...[truncado]...\n" + content[-_CONTENT_TAIL:]
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                tc_parts: list[str] = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args = fn.get("arguments", "")
                        if len(args) > _TOOL_ARGS_MAX:
                            args = args[:_TOOL_ARGS_MAX] + "..."
                        tc_parts.append(f"  {name}({args})")
                content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
            parts.append(f"[ASSISTANT]: {content}")
            continue

        if len(content) > _CONTENT_MAX:
            content = content[:_CONTENT_HEAD] + "\n...[truncado]...\n" + content[-_CONTENT_TAIL:]
        parts.append(f"[{role.upper()}]: {content}")

    return "\n\n".join(parts)


def _summarize_llm_structured(
    client: Any,
    model: str,
    messages: list[dict],
    *,
    previous_summary: str | None = None,
) -> tuple[str, bool]:
    """Compressão semântica estruturada via LLM. Retorna (summary_text, success)."""
    content_tokens = sum(len(m.get("content") or "") for m in messages) // _CHARS_PER_TOKEN
    budget = max(_MIN_SUMMARY_TOKENS, min(int(content_tokens * _SUMMARY_RATIO), _SUMMARY_TOKENS_CEILING))

    serialized = _serialize_for_summary(messages)

    _preamble = (
        "Você é um agente de sumarização criando um checkpoint de contexto. "
        "Trate os turnos de conversa abaixo como material-fonte para um registro compacto. "
        "Produza apenas o sumário estruturado; não adicione saudações ou prefixo. "
        "Escreva no mesmo idioma do usuário. "
        "NUNCA inclua API keys, tokens, passwords ou secrets no sumário."
    )

    _template = f"""## Tarefa Ativa
[O campo mais importante. Capture a entrada mais recente NÃO respondida do usuário de forma exata.
Inclui perguntas, decisões pendentes e tarefas em aberto. Reserve "Nenhuma." apenas para o raro caso
em que o último turno foi totalmente resolvido.]

## Objetivo
[O que o usuário está tentando alcançar no geral]

## Restrições e Preferências
[Preferências de código, estilo, decisões importantes, constraints]

## Ações Concluídas
[Lista numerada: AÇÃO no alvo — resultado [tool: nome]
Ex: 1. LEIA config.py:45 — encontrou bug na linha 45 [tool: read_file]
    2. PATCH config.py:45 — corrigiu `==` para `!=` [tool: patch]
Seja específico com paths, comandos, linha e resultado.]

## Estado Atual
[Estado atual: diretório, branch, arquivos modificados, testes, processos rodando]

## Em Progresso
[Trabalho em andamento quando a compressão disparou]

## Bloqueadores
[Erros não resolvidos com mensagens exatas]

## Decisões Chave
[Decisões técnicas importantes e POR QUÊ foram tomadas]

## Perguntas Resolvidas
[Perguntas já respondidas com a resposta — para não repetir]

## Arquivos Relevantes
[Arquivos lidos, modificados ou criados com breve nota]

## Trabalho Restante
[O que ainda precisa ser feito — como contexto, não instruções]

## Contexto Crítico
[Valores específicos, mensagens de erro, detalhes de config que seriam perdidos sem preservação explícita. NUNCA inclua credenciais — use [REDACTED].]

Meta ~{budget} tokens. Seja CONCRETO — paths, saídas de comandos, números de linha. Evite descrições vagas.
Escreva apenas o corpo do sumário, sem prefixo."""

    if previous_summary:
        prompt = f"""{_preamble}

Você está atualizando um sumário de compressão de contexto. Uma compressão anterior produziu o sumário abaixo.
Novos turnos precisam ser incorporados.

SUMÁRIO ANTERIOR:
{previous_summary}

NOVOS TURNOS A INCORPORAR:
{serialized}

Atualize o sumário usando a mesma estrutura. PRESERVE informações existentes ainda relevantes.
ADICIONE novas ações concluídas à lista numerada. Mova itens de "Em Progresso" para "Ações Concluídas" quando feito.
ATUALIZE "Estado Atual". REMOVA informações apenas se claramente obsoletas.
ATUALIZE "## Tarefa Ativa" para refletir a entrada mais recente não respondida.

{_template}"""
    else:
        prompt = f"""{_preamble}

Crie um checkpoint estruturado do sumário para a conversa após turnos anteriores serem compactados.

TURNOS A SUMARIZAR:
{serialized}

Use esta estrutura exata:

{_template}"""

    try:
        parts: list[str] = []
        for chunk in client.chat_stream(model, [{"role": "user", "content": prompt}]):
            parts.append(chunk)
        result = "".join(parts).strip()
        if result:
            return result, True
        return _summarize_messages(messages), False
    except Exception:
        return "", False


def _summarize_messages(messages: list[dict]) -> str:
    """Gera resumo textual de mensagens sem chamar o LLM (fallback rule-based).

    Extrai: quantidade de turnos, tópicos mencionados (palavras longas frequentes),
    tools chamadas, e últimas decisões visíveis.
    """
    user_msgs = [m["content"] for m in messages if m.get("role") == "user" and m.get("content") is not None]
    assistant_msgs = [m["content"] for m in messages if m.get("role") == "assistant" and m.get("content") is not None]

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
        for word in re.findall(r"\b[a-zA-ZÀ-ú]{5,30}\b", clean.lower()):
            if word not in stopwords and len(set(word)) > 1:
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
