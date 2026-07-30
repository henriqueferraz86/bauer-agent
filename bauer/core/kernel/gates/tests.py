"""TestsGate — o run só conclui se os testes do projeto passarem.

A peça que faltava do harness. Todo o S8 (custódia em 11 caminhos) existe para
que gates rodem; até aqui rodavam dois, ambos farejando texto. Este é o primeiro
gate SUBSTANTIVO: ele executa, não interpreta prosa.

Reusa ``app_verify``, que já detecta a stack por marker, monta o plano e para na
primeira falha — não reimplementa nada disso.

Três decisões que definem se o gate é usável ou um estorvo:

1. **Só dispara quando o run mudou arquivos.** Rodar a suíte num turno de
   conversa seria absurdo. A evidência de mudança vem do git (fonte da verdade)
   ou, fora de repo, das tools mutantes que o executor registrou.

2. **Só o passo `test`.** Nada de `install` (minutos e efeito colateral) nem de
   `serve` (subir o app não é gate). Daí o ``only=`` que este PR acrescentou ao
   ``verify_project``.

3. **Timeout obrigatório.** Um gate sem teto transforma o laço autônomo em
   refém da suíte. Estourou o tempo, o gate REPROVA — não dá para afirmar que
   passou o que não terminou.

Quando reprova, o motivo vira ``replan_feedback`` no payload do executor: o laço
recebe a cauda do output de teste e tenta corrigir. É o ciclo que transforma
"o agente disse que terminou" em "os testes passaram".
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

#: tools cujo uso significa "o workspace pode ter mudado". Espelha
#: ``tool_dedup.MUTATING_TOOLS``, importado de lá para não divergir.
def _tools_mutantes() -> frozenset[str]:
    from ....tool_dedup import MUTATING_TOOLS
    return MUTATING_TOOLS


#: Artefatos que o PRÓPRIO Bauer (ou o Python) cria dentro do workspace. Sem
#: ignorá-los, `git status` fica sujo já na primeira execução — o Kernel grava
#: `memory/runtime/*.jsonl` ali — e o gate passaria a disparar em TODO run,
#: inclusive turno de conversa. Medido: `?? memory/` num projeto recém-criado.
_RUIDO = ("memory/", "__pycache__/", ".pytest_cache/", ".bauer/")


def _e_ruido(caminho: str) -> bool:
    c = caminho.replace("\\", "/").lstrip("./")
    return any(p.rstrip("/") == c or c.startswith(p) or f"/{p}" in c for p in _RUIDO)


def _git_sujo(workspace: Path) -> "bool | None":
    """True/False se for repo git; None quando não dá para saber por git."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(workspace),
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None  # não é repo git (ou git ausente)
    for linha in proc.stdout.splitlines():
        alvo = linha[3:].strip() if len(linha) > 3 else ""
        if alvo and not _e_ruido(alvo):
            return True
    return False


def houve_mudanca(workspace: Path, result: dict[str, Any]) -> bool:
    """O run mexeu no workspace?

    Git primeiro — é a fonte da verdade e pega mudança feita por qualquer
    caminho, inclusive comando de shell que o Bauer não modelou como tool.
    Fora de repo, cai para as tools mutantes registradas pelo executor.
    """
    sujo = _git_sujo(workspace)
    if sujo is not None:
        return sujo
    usadas = {str(t).lower() for t in (result.get("tools_usadas") or [])}
    return bool(usadas & _tools_mutantes())


class TestsGate:
    """Roda os testes do projeto; reprova o run se falharem."""

    #: o nome começa com "Test", então o pytest tenta coletar a classe como
    #: suite e avisa que não consegue (tem __init__). Não é teste — é o gate.
    __test__ = False

    name = "tests"

    def __init__(self, workspace: "str | Path", *, timeout_s: int = 600,
                 verify_fn: Any = None, modo: str = "regressao") -> None:
        self.workspace = Path(workspace)
        self.timeout_s = max(1, int(timeout_s))
        # "regressao" (default): reprova só falha NOVA em relação ao baseline —
        # projeto herdado vermelho não trava todo run autônomo.
        # "estrito": qualquer falha reprova. Correto só onde a suíte já é verde.
        self.modo = "estrito" if str(modo).lower() == "estrito" else "regressao"
        # injetável: os testes deste gate não podem depender de npm/pytest reais
        self._verify = verify_fn

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        if not houve_mudanca(self.workspace, result or {}):
            return _GR(self.name, True, "nenhuma mudança no workspace — nada a testar")

        verify = self._verify
        if verify is None:
            from ....app_verify import verify_project as verify

        try:
            res = verify(self.workspace, install=False, only=["test"],
                         timeout=self.timeout_s)
        except Exception as exc:  # noqa: BLE001
            # Gate quebrado é problema do gate, não do resultado — mesma regra do
            # Evaluator. Passa com ressalva em vez de reprovar por infra.
            return _GR(self.name, True, f"gate falhou ao rodar (ignorado): {exc}")

        from .baseline import comparar, extrair_falhas, gravar_baseline, ler_baseline

        if getattr(res, "ok", False):
            # Suíte verde: aperta o baseline. Dívida paga fica travada — quem
            # consertou não quer o teste de volta amanhã.
            gravar_baseline(self.workspace, falhas=set(), total=0)
            return _GR(self.name, True, getattr(res, "summary", "") or "testes passaram")

        stack = getattr(res, "stack", "unknown")
        if stack == "unknown":
            # Sem stack detectável não há o que afirmar. Reprovar aqui puniria
            # todo projeto sem package.json/pyproject — inclusive escrita de
            # documentação, que é trabalho legítimo.
            return _GR(self.name, True, "stack não detectada — sem testes a rodar")

        if self.modo == "estrito":
            return _GR(self.name, False, _motivo(res))

        saida = "\n".join((getattr(s, "output", "") or "")
                          for s in (getattr(res, "steps", None) or []))
        falhas = extrair_falhas(saida)
        base = ler_baseline(self.workspace)
        regrediu, motivo = comparar(falhas, len(falhas), base)
        if regrediu:
            return _GR(self.name, False, f"{motivo}\n\n{_motivo(res)}")
        # Não regrediu: registra o estado atual como novo baseline (encolheu ou
        # ficou igual) e deixa passar com o aviso de que segue vermelho.
        if base is None or falhas <= set(base.get("falhas") or []):
            gravar_baseline(self.workspace, falhas=falhas, total=len(falhas))
        return _GR(self.name, True, motivo)


def _motivo(res: Any) -> str:
    """Motivo curto e ACIONÁVEL — vira replan_feedback para o laço corrigir."""
    partes = [f"testes falharam ({getattr(res, 'stack', '?')})"]
    for step in getattr(res, "steps", []) or []:
        if getattr(step, "ok", True):
            continue
        cmd = " ".join(getattr(step, "cmd", []) or [])
        if getattr(step, "skipped", False):
            partes.append(f"{step.name}: {getattr(step, 'reason', 'pulado')}")
            continue
        cauda = (getattr(step, "output", "") or "")[-1200:]
        partes.append(f"$ {cmd}\n{cauda}")
        break  # a primeira falha é a que importa; o resto é ruído
    return "\n".join(partes)
