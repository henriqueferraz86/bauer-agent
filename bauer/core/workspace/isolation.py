"""Isolamento por tarefa — o run autônomo trabalha num worktree, não no master.

S12 nível 1. Reusa ``task_worktree.py``, que já existe e é usado pelo dispatcher:
cria um ``git worktree`` num branch dedicado, roda ali, e ao final commita — o
artefato da tarefa vira um diff reviewável num branch isolado.

**A regra vem do contrato, não é geral.** O plano original dizia "nenhum
`bauer run` altera diretamente a branch principal". Isso quebraria o fluxo de
trabalho diário deste projeto, onde fix pequeno vai direto ao master sem branch
nem PR. Aqui quem decide é ``TaskContract.isolation``:

    none       (default) trabalha no workspace atual, como hoje
    worktree   branch próprio; o master não é tocado
    container  ainda não implementado — degrada para worktree com aviso

Sem contrato, sem isolamento: o comportamento de hoje fica intocado por omissão,
não por acidente.

Degradação: workspace fora de repo git vira no-op silencioso — mesmo padrão do
``task_worktree``. Não dá para isolar o que não é versionado, e recusar a rodar
seria pior que rodar sem isolamento.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Isolamento:
    """Ambiente resolvido para o run."""

    #: onde o agente vai efetivamente trabalhar
    workspace: Path
    #: None = sem isolamento (trabalhando no workspace original)
    worktree: Any = None
    #: por que não isolou, quando o contrato pedia (para o banner ser honesto)
    aviso: str = ""

    @property
    def isolado(self) -> bool:
        return self.worktree is not None


def preparar(workspace: "str | Path", contrato: Any, *, run_id: str,
             bus: Any = None) -> Isolamento:
    """Resolve o ambiente de trabalho a partir do contrato.

    ``bus`` opcional: publica ``run.workspace.created`` quando o worktree nasce.
    O evento é o único registro de que o trabalho NÃO aconteceu onde o usuário
    está olhando — sem ele, um diff que "sumiu" do master é um mistério em vez
    de uma linha de auditoria.
    """
    ws = Path(workspace)
    nivel = str(getattr(contrato, "isolation", "none") or "none").lower()
    if contrato is None or nivel in ("", "none"):
        return Isolamento(workspace=ws)

    aviso = ""
    if nivel == "container":
        # Ser explícito é melhor que fingir que containeriza. O plano põe
        # container em P1 (S12 nível 2); até lá, worktree é o que existe.
        aviso = "isolation: container ainda não implementado — usando worktree"
        nivel = "worktree"

    if nivel != "worktree":
        return _degradou(bus, run_id, ws, f"isolation '{nivel}' desconhecido — ignorado")

    from ...task_worktree import create_worktree, is_git_repo

    if not is_git_repo(ws):
        return _degradou(bus, run_id, ws,
                         "isolation: worktree pedido, mas o workspace não é repo git")

    wt = create_worktree(ws, _slug(run_id))
    if wt is None:
        return _degradou(bus, run_id, ws,
                         "falha ao criar o worktree — rodando sem isolamento")

    _copiar_contrato(ws, wt.path)
    _emitir(bus, "run.workspace.created", run_id, status="worktree", message=aviso,
            data={"branch": getattr(wt, "branch", ""), "path": str(wt.path),
                  "origem": str(ws)})
    return Isolamento(workspace=wt.path, worktree=wt, aviso=aviso)


def finalizar(iso: Isolamento, *, objetivo: str, sucesso: bool,
              preservar_em_falha: bool = True, bus: Any = None,
              run_id: str = "") -> str:
    """Commita o trabalho e devolve a linha de artefato. "" quando não isolou.

    Falha PRESERVA o worktree por default: o diff parcial é a evidência de onde
    o agente parou, e jogá-lo fora custa mais que o disco que ocupa.

    ``run.workspace.cleaned`` sai nos DOIS desfechos, com ``removido`` dizendo
    qual foi. Emitir só na remoção faria o evento significar "worktree sumiu" em
    vez de "o isolamento terminou" — e o caso que mais importa auditar é
    justamente o worktree que FICOU, ocupando disco à espera de revisão.
    """
    if not iso.isolado:
        return ""
    from ...task_worktree import commit_worktree, remove_worktree, summarize_artifact

    commit = commit_worktree(iso.worktree, f"bauer: {objetivo}"[:200])
    linha = summarize_artifact(commit)
    removido = not commit.committed and (sucesso or not preservar_em_falha)
    if removido:
        # nada mudou: worktree vazio só polui o repo
        remove_worktree(iso.worktree)
    _emitir(bus, "run.workspace.cleaned", run_id,
            status="removido" if removido else "preservado",
            data={"branch": getattr(iso.worktree, "branch", ""),
                  "path": str(iso.workspace), "removido": removido,
                  "commitou": bool(commit.committed), "sucesso": sucesso})
    return "" if removido else linha


def _degradou(bus: Any, run_id: str, ws: Path, aviso: str) -> Isolamento:
    """Contrato pediu isolamento e não teve. Vira evento, não só texto no console.

    O banner amarelo some com o scrollback; a promessa "o master não é tocado"
    não pode depender de alguém ter lido a tela na hora.
    """
    _emitir(bus, "run.workspace.created", run_id, status="degradado", message=aviso,
            data={"path": str(ws), "isolado": False})
    return Isolamento(workspace=ws, aviso=aviso)


def _emitir(bus: Any, tipo: str, run_id: str, **campos: Any) -> None:
    from ..events.emit import emitir

    emitir(bus, tipo, run_id=run_id or None, **campos)


def _slug(run_id: str) -> str:
    limpo = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(run_id))
    return limpo.strip("-")[:48] or "run"


def _copiar_contrato(origem: Path, destino: Path) -> None:
    """Leva ``.bauer/task.yaml`` para o worktree.

    O contrato costuma estar NÃO versionado (é da tarefa, não do projeto), então
    não aparece no worktree novo. Sem ele lá, os gates rodariam sem escopo —
    isolamento sem escopo é meia governança.
    """
    src = origem / ".bauer" / "task.yaml"
    if not src.is_file():
        return
    try:
        dst = destino / ".bauer" / "task.yaml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 — cópia é conveniência, não requisito
        from ...logging_config import log_suppressed
        log_suppressed("isolation.copiar_contrato", exc)


def contrato_do_workspace(workspace: "str | Path") -> Optional[Any]:
    """Atalho para quem só precisa do contrato antes de decidir o ambiente."""
    from ..task import TaskContract

    return TaskContract.descobrir(workspace)
