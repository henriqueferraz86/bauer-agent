"""SecretsGate — segredo no diff impede o run de concluir.

Embrulha ``secrets_scanner``, que já existe e já é usado na saída do gateway. O
que faltava era rodá-lo sobre **o que o run escreveu**, antes de o run virar
`completed` — e portanto antes de o worktree ser commitado (§12) ou o resultado
ser reportado como bom.

Por que sobre o DIFF e não sobre a árvore:

- um repositório herdado pode ter segredo antigo em arquivo que a tarefa nem
  encostou. Reprovar por causa dele travaria todo run no projeto, para sempre,
  por uma dívida que o agente não criou — o mesmo erro que o ratchet do gate de
  testes existe para evitar.
- o que importa julgar é a AÇÃO deste run. `git diff` responde exatamente isso.

Só as linhas ADICIONADAS entram no escaneamento. Uma linha removida que continha
segredo é o agente **consertando** o problema; reprovar ali seria punir a
correção e, na prática, ensinar o agente a não mexer em arquivo com segredo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def linhas_adicionadas(workspace: Path, *, timeout_s: int = 30) -> "str | None":
    """Texto das linhas `+` do diff (working tree + staged + untracked).

    None quando não é repo git — sem diff não há o que afirmar.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD", "--unified=0", "--no-color"],
            cwd=str(workspace), capture_output=True, text=True,
            timeout=timeout_s, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    partes = [ln[1:] for ln in proc.stdout.splitlines()
              if ln.startswith("+") and not ln.startswith("+++")]

    # Arquivo NOVO não versionado não aparece em `git diff HEAD` — e é
    # justamente onde um `.env` ou um `config.local.yaml` recém-criado apareceria.
    # Deixá-lo de fora seria abrir o buraco exatamente no caso mais provável.
    partes.extend(_untracked(workspace, timeout_s=timeout_s))
    return "\n".join(partes)


def _untracked(workspace: Path, *, timeout_s: int) -> list[str]:
    from .tests import _e_ruido

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(workspace), capture_output=True, text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    saida: list[str] = []
    for rel in proc.stdout.splitlines():
        rel = rel.strip()
        if not rel or _e_ruido(rel):
            continue
        alvo = workspace / rel
        try:
            if alvo.stat().st_size > 1_000_000:
                continue   # binário/dump: o scanner é de texto
            saida.append(alvo.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return saida


class SecretsGate:
    """Reprova o run que introduziu credencial no código."""

    name = "secrets"

    def __init__(self, workspace: "str | Path", *, diff_fn: Any = None,
                 timeout_s: int = 30) -> None:
        self.workspace = Path(workspace)
        self.timeout_s = max(1, int(timeout_s))
        self._diff = diff_fn or linhas_adicionadas

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        adicionado = self._diff(self.workspace)
        if adicionado is None:
            return _GR(self.name, True, "workspace não é repo git — sem diff a escanear")
        if not adicionado.strip():
            return _GR(self.name, True, "nada adicionado")

        from ....secrets_scanner import scan

        res = scan(adicionado, redact=False)
        if not res.found:
            return _GR(self.name, True, "nenhum segredo nas linhas adicionadas")

        # O motivo vira `replan_feedback` e vai para o log. Nome e prefixo bastam
        # para o agente achar a linha; o valor inteiro NÃO entra — reprovar por
        # vazamento e vazar no motivo seria o pior desfecho possível.
        vistos: dict[str, str] = {}
        for m in res.matches:
            vistos.setdefault(m["name"], m["preview"])
        detalhe = "\n".join(f"  - {nome} (começa com {prev!r})"
                            for nome, prev in list(vistos.items())[:10])
        return _GR(
            self.name, False,
            f"{len(res.matches)} possível(is) segredo(s) nas linhas adicionadas "
            f"por este run:\n{detalhe}\n"
            f"Remova o valor do código e leia-o de variável de ambiente.",
        )
