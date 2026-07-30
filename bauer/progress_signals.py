"""Sinais de estagnação que só aparecem entre RODADAS, não entre tool calls.

`tool_guardrails` já cobre seis dos nove sinais do §13 do plano, e cobre bem —
mas todos por CHAMADA: mesma tool com mesmos args, mesmo erro repetido, leitura
idempotente. Os três que faltavam têm outra escala. Nenhuma chamada individual
parece errada; o que está errado é a sequência.

    alterações revertidas          o agente escreve, desfaz, reescreve. Cada
                                   chamada é legítima e distinta — só a
                                   trajetória mostra que ele está andando em
                                   círculo.
    tokens crescendo sem progresso a janela enche, o custo sobe, e o disco não
                                   muda. É o modo de falha mais caro do laço
                                   autônomo: gasta o orçamento inteiro
                                   raciocinando sobre o mesmo estado.
    plano sem mudança entre replans (fica no Kernel — replan é decisão dele,
                                   não do laço de rodadas)

**Detectar não é interromper.** Estes sinais AVISAM: entram no contexto como
nudge e viram `run.progress.warning` na auditoria. Quem para o laço continua
sendo o orçamento e os guardrails de chamada. Um detector heurístico com poder
de matar a tarefa erraria contra trabalho legítimo — refatoração grande passa
por estados intermediários que parecem exatamente com isto.

Fonte de verdade do estado: git. Fora de repo, o detector se desliga em vez de
adivinhar — é a mesma regra dos gates.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Rodadas sem qualquer mudança no workspace antes de avisar sobre tokens.
#: Duas seriam ruído puro — pensar antes de agir é normal, e a primeira rodada
#: quase sempre é leitura.
RODADAS_PARADAS_PARA_AVISAR = 3

#: Crescimento de contexto que torna "parado" caro o bastante para avisar.
CRESCIMENTO_MINIMO_TOKENS = 2000


@dataclass
class Aviso:
    code: str
    message: str
    data: dict = field(default_factory=dict)


def _estado_do_workspace(workspace: Path, *, timeout_s: int = 15) -> "str | None":
    """Digest do trabalho não commitado. None fora de repo git.

    Hasheia o PATCH (`git diff HEAD`), não a lista de arquivos: com a lista, o
    agente que muda `a.py` de duas formas diferentes produziria o mesmo estado
    (` M a.py` nas duas) e apareceria como reversão. O patch distingue.
    """
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--no-color"], cwd=str(workspace),
            capture_output=True, text=True, timeout=timeout_s, errors="replace")
        novos = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(workspace), capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode != 0:
        return None

    from .core.kernel.gates.tests import _e_ruido

    h = hashlib.sha256(diff.stdout.encode("utf-8", "replace"))
    for rel in sorted(novos.stdout.splitlines()):
        rel = rel.strip()
        if not rel or _e_ruido(rel):
            continue
        try:
            tamanho = (workspace / rel).stat().st_size
        except OSError:
            tamanho = -1
        h.update(f"\0{rel}:{tamanho}".encode("utf-8", "replace"))
    return h.hexdigest()


class SinaisDeProgresso:
    """Alimentado uma vez por rodada; devolve os avisos daquela rodada."""

    def __init__(self, workspace: "str | Path", *, ctx: Any = None,
                 bus: Any = None, run_id: str = "", session_id: str = "",
                 estado_fn: Any = None) -> None:
        self.workspace = Path(workspace)
        self.ctx = ctx
        self.bus = bus
        self.run_id = run_id
        self.session_id = session_id
        self._estado = estado_fn or _estado_do_workspace
        #: digest -> primeira rodada em que apareceu
        self._vistos: dict[str, int] = {}
        self._ultimo: "str | None" = None
        self._rodada_da_ultima_mudanca = 0
        self._tokens_na_ultima_mudanca = 0
        self._ja_avisou_tokens = False
        self._revertidos_avisados: set[str] = set()

    # ── entrada ──────────────────────────────────────────────────────────────

    def rodada(self, numero: int, texto: str = "",
               tool_log: "list[dict] | None" = None) -> list[Aviso]:
        estado = self._estado(self.workspace)
        if estado is None:
            return []   # fora de repo git: não se adivinha

        avisos: list[Aviso] = []
        if estado != self._ultimo:
            avisos.extend(self._checar_reversao(estado, numero))
            self._ultimo = estado
            self._vistos.setdefault(estado, numero)
            self._rodada_da_ultima_mudanca = numero
            self._tokens_na_ultima_mudanca = self._tokens()
            self._ja_avisou_tokens = False
        else:
            avisos.extend(self._checar_tokens(numero))

        for a in avisos:
            self._publicar(a)
        return avisos

    # ── os dois sinais ───────────────────────────────────────────────────────

    def _checar_reversao(self, estado: str, numero: int) -> list[Aviso]:
        anterior = self._vistos.get(estado)
        if anterior is None or estado in self._revertidos_avisados:
            return []
        self._revertidos_avisados.add(estado)
        return [Aviso(
            "reverted_changes",
            f"[WARN] o workspace voltou ao estado exato da rodada {anterior} "
            f"(agora {numero}): as alterações feitas no meio foram desfeitas. "
            f"Se a abordagem não funcionou, tente outra — repeti-la vai dar no "
            f"mesmo lugar.",
            {"rodada_original": anterior, "rodada_atual": numero},
        )]

    def _checar_tokens(self, numero: int) -> list[Aviso]:
        paradas = numero - self._rodada_da_ultima_mudanca
        if paradas < RODADAS_PARADAS_PARA_AVISAR or self._ja_avisou_tokens:
            return []
        crescimento = self._tokens() - self._tokens_na_ultima_mudanca
        if crescimento < CRESCIMENTO_MINIMO_TOKENS:
            return []
        self._ja_avisou_tokens = True
        return [Aviso(
            "token_growth_no_progress",
            f"[WARN] {paradas} rodadas sem NENHUMA alteração no workspace, com "
            f"~{crescimento} tokens a mais de contexto. O estado do disco é o "
            f"mesmo de antes — se o próximo passo é uma edição, faça-a; se está "
            f"faltando informação, o contexto atual já não a tem.",
            {"rodadas_paradas": paradas, "tokens_a_mais": crescimento},
        )]

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _tokens(self) -> int:
        try:
            return int(getattr(self.ctx, "used_tokens", 0) or 0)
        except Exception:  # noqa: BLE001 — medida de contexto nunca derruba o laço
            return 0

    def _publicar(self, aviso: Aviso) -> None:
        from .core.events.emit import emitir

        emitir(self.bus, "run.progress.warning",
               run_id=self.run_id or None, session_id=self.session_id or None,
               status="warn", message=aviso.message,
               data={"code": aviso.code, **aviso.data})
