"""ContextBuilder — uma porta para o que entra na janela, com proveniência.

O último indicador em aberto do §15. O S7 mediu o problema: ``ContextManager`` é
instanciado em **10 lugares independentes**, cada um com parâmetros próprios —
`agent.py`, `chat.py`, `run_cmd`, `server.py`, `orchestrator` (×2),
`channel_base`, `benchmark` (×2) — e `_build_system_prompt` monta o prompt por
fora de todos eles.

**Este módulo NÃO reescreve o compressor.** `context_manager.py` resolve budget
por provider, compressão semântica com fallback rule-based, anti-thrashing, tail
protection dinâmica e pruning de tool results — tudo ganho a duras penas, com
bugs reais por trás de cada regra. Reescrever seria regressão garantida. Aqui há
uma **fachada** que delega para ele e acrescenta o que faltava:

1. **Proveniência.** Todo item declara de onde veio e se é INSTRUÇÃO confiável ou
   CONTEÚDO informativo. Sem isso não há como provar que texto de arquivo não
   virou system prompt — e "conteúdo malicioso tenta virar instrução" é o
   cenário 15 da Evaluation Suite.
2. **Uma origem só.** Quem constrói contexto passa a pedir aqui, e o que cada
   fonte gastou fica registrado.

O que o S7 já resolveu e aqui é só preservado: o modo de tool calling vem do
CLIENTE VIVO, nunca de default — foi o default `"bridge"` que deixou o agente
intermitente por semanas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Ordem de prioridade do plano (§8). Menor número entra primeiro e é o último
#: a ser cortado quando o orçamento aperta.
PRIORIDADE = {
    "seguranca": 1,
    "contrato": 2,
    "aceite": 3,
    "escopo": 4,
    "arquivos": 5,
    "erros": 6,
    "decisoes": 7,
    "sessoes": 8,
    "documentacao": 9,
}


@dataclass
class ItemContexto:
    """Um pedaço do contexto, com a origem registrada."""

    fonte: str
    conteudo: str
    #: True = INSTRUÇÃO (pode virar system). False = CONTEÚDO (nunca vira).
    confiavel: bool = False
    prioridade: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        from ...context_manager import CHARS_PER_TOKEN

        return max(1, len(self.conteudo) // CHARS_PER_TOKEN)


@dataclass
class PacoteContexto:
    """O que foi montado, e quanto cada fonte custou."""

    itens: list[ItemContexto] = field(default_factory=list)
    descartados: int = 0

    @property
    def total_tokens(self) -> int:
        return sum(i.tokens for i in self.itens)

    def por_fonte(self) -> dict[str, int]:
        saida: dict[str, int] = {}
        for i in self.itens:
            saida[i.fonte] = saida.get(i.fonte, 0) + i.tokens
        return saida

    def instrucoes(self) -> list[ItemContexto]:
        return [i for i in self.itens if i.confiavel]

    def conteudo_informativo(self) -> list[ItemContexto]:
        return [i for i in self.itens if not i.confiavel]

    def to_dict(self) -> dict[str, Any]:
        return {"context_tokens": self.total_tokens, "sources": self.por_fonte(),
                "discarded_items": self.descartados, "items": len(self.itens)}


class ContextBuilder:
    """Monta o contexto de um turno e devolve um ``ContextManager`` pronto.

    A fachada existe para haver **um** lugar que decide o que entra — não para
    substituir o motor que já funciona.
    """

    def __init__(self, *, applied_context: int, provider: str = "ollama",
                 orcamento_max_ratio: float = 1.0) -> None:
        self.applied_context = int(applied_context)
        self.provider = provider
        self.orcamento_max_ratio = orcamento_max_ratio
        self._itens: list[ItemContexto] = []
        self._descartados = 0

    # ── entrada ──────────────────────────────────────────────────────────────

    def instrucao(self, fonte: str, conteudo: str, *, prioridade: int | None = None,
                  **meta: Any) -> "ContextBuilder":
        """Texto que o Bauer controla — pode virar system prompt."""
        return self._add(fonte, conteudo, True, prioridade, meta)

    def conteudo(self, fonte: str, texto: str, *, prioridade: int | None = None,
                 **meta: Any) -> "ContextBuilder":
        """Texto vindo de FORA (arquivo, web, tool). Nunca vira instrução.

        A separação é o ponto: um README que diga "ignore as regras anteriores"
        entra como dado, não como ordem.
        """
        return self._add(fonte, texto, False, prioridade, meta)

    def _add(self, fonte: str, texto: str, confiavel: bool,
             prioridade: int | None, meta: dict) -> "ContextBuilder":
        if not (texto or "").strip():
            self._descartados += 1
            return self
        self._itens.append(ItemContexto(
            fonte=fonte, conteudo=texto, confiavel=confiavel,
            prioridade=prioridade if prioridade is not None
            else PRIORIDADE.get(fonte, 5),
            metadata=meta))
        return self

    # ── saída ────────────────────────────────────────────────────────────────

    def pacote(self) -> PacoteContexto:
        """Itens ordenados por prioridade, sem duplicata, dentro do orçamento."""
        vistos: set[str] = set()
        ordenados = sorted(self._itens, key=lambda i: (i.prioridade, i.fonte))
        mantidos: list[ItemContexto] = []
        descartados = self._descartados
        teto = int(self.applied_context * 0.75 * self.orcamento_max_ratio)
        gasto = 0
        for item in ordenados:
            chave = item.conteudo.strip()
            if chave in vistos:
                descartados += 1     # mesma coisa vinda de duas fontes
                continue
            if teto and gasto + item.tokens > teto and not item.confiavel:
                # Instrução nunca é cortada por orçamento: sem ela o modelo perde
                # as regras e o corte que economizaria tokens custa a tarefa.
                descartados += 1
                continue
            vistos.add(chave)
            mantidos.append(item)
            gasto += item.tokens
        return PacoteContexto(itens=mantidos, descartados=descartados)

    def montar(self, *, llm_client: Any = None, llm_model: str = ""):
        """(ContextManager, PacoteContexto).

        O system prompt sai só das INSTRUÇÕES; conteúdo informativo entra como
        mensagem de usuário. É o que torna verificável a promessa de que arquivo
        não vira ordem.
        """
        from ...context_manager import ContextManager

        pac = self.pacote()
        sistema = "\n\n".join(i.conteudo for i in pac.instrucoes())
        ctx = ContextManager(applied_context=self.applied_context,
                             system_prompt=sistema or None,
                             provider=self.provider)
        if llm_client is not None:
            ctx.set_llm(llm_client, llm_model)
        for item in pac.conteudo_informativo():
            ctx.add_user(item.conteudo)
        return ctx, pac
