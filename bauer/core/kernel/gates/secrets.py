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

K07 (auditoria 2026-10-12): três situações que antes viravam a mesma
"não é repo git — passou" agora são distintas:

- **não é repositório** (nenhum ``.git`` no workspace ou acima): passa, sem
  diff não há o que afirmar;
- **repositório sem HEAD** (``git init`` sem primeiro commit): compara index e
  árvore de trabalho com a árvore vazia — tudo é novo — e escaneia untracked;
- **erro operacional** (git ausente num repo, timeout, diff/ls-files falhando,
  arquivo novo ilegível): ``ScannerIndisponivel`` → o gate crítico REPROVA com
  o motivo explícito. Não conseguir olhar não é o mesmo que não achar.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class ScannerIndisponivel(RuntimeError):
    """O workspace É um repositório Git, mas não deu para listar o que mudou."""


def _git(workspace: Path, args: list[str], *, timeout_s: int,
         input_text: "str | None" = None) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            ["git", *args], cwd=str(workspace), capture_output=True, text=True,
            timeout=timeout_s, errors="replace", input=input_text,
        )
    except FileNotFoundError as exc:
        raise ScannerIndisponivel("git não encontrado no PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScannerIndisponivel(f"git {args[0]} excedeu {timeout_s}s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScannerIndisponivel(f"git {args[0]} falhou: {type(exc).__name__}") from exc


def _parece_repo(workspace: Path) -> bool:
    """Há um ``.git`` (diretório, ou arquivo de worktree) no workspace ou acima?

    Decide "não é repo" sem depender de mensagem de erro localizada do git nem
    de o binário existir: é a presença do repositório, não a do git, que diz se
    há diff a escanear.
    """
    try:
        atual = workspace.resolve()
    except OSError:
        atual = workspace.absolute()
    return any((pasta / ".git").exists() for pasta in (atual, *atual.parents))


def _arvore_vazia(workspace: Path, *, timeout_s: int) -> str:
    """Id da árvore vazia no formato de hash DESTE repo (sha1 ou sha256)."""
    proc = _git(workspace, ["hash-object", "-t", "tree", "--stdin"],
                timeout_s=timeout_s, input_text="")
    oid = proc.stdout.strip()
    if proc.returncode != 0 or not oid:
        raise ScannerIndisponivel("git hash-object da árvore vazia falhou")
    return oid


def _base(workspace: Path, *, timeout_s: int) -> str:
    """HEAD quando existe; senão a árvore vazia (repo sem primeiro commit)."""
    repo = _git(workspace, ["rev-parse", "--git-dir"], timeout_s=timeout_s)
    if repo.returncode != 0:
        raise ScannerIndisponivel(
            f"git rev-parse falhou num workspace com .git (exit {repo.returncode})")
    head = _git(workspace, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                timeout_s=timeout_s)
    if head.returncode == 0 and head.stdout.strip():
        return "HEAD"
    # HEAD inexistente (git init sem commit): tudo que está no index ou na
    # árvore de trabalho é novo. Comparar com a árvore vazia escaneia MAIS,
    # nunca menos — um HEAD corrompido lido como "sem HEAD" falha seguro.
    return _arvore_vazia(workspace, timeout_s=timeout_s)


def linhas_adicionadas(workspace: Path, *, timeout_s: int = 30) -> "str | None":
    """Texto das linhas `+` do diff (staged + working tree + untracked).

    None SÓ quando o workspace não é repositório Git. Repositório que não pôde
    ser lido levanta ``ScannerIndisponivel``.
    """
    workspace = Path(workspace)
    if not _parece_repo(workspace):
        return None
    base = _base(workspace, timeout_s=timeout_s)

    partes: list[str] = []
    # `--cached` = o index (o que o próximo commit levaria); sem ele = árvore de
    # trabalho. Um segredo staged e depois apagado só da árvore de trabalho
    # ainda seria commitado — por isso os dois.
    for extra in (["--cached"], []):
        proc = _git(workspace, ["diff", *extra, "--unified=0", "--no-color",
                                "--no-ext-diff", "--no-textconv", base, "--"],
                    timeout_s=timeout_s)
        if proc.returncode != 0:
            raise ScannerIndisponivel(
                f"git diff {' '.join(extra) or '(worktree)'} falhou "
                f"(exit {proc.returncode})")
        partes.extend(ln[1:] for ln in proc.stdout.splitlines()
                      if ln.startswith("+") and not ln.startswith("+++"))

    # Arquivo NOVO não versionado não aparece em `git diff` — e é
    # justamente onde um `.env` ou um `config.local.yaml` recém-criado apareceria.
    # Deixá-lo de fora seria abrir o buraco exatamente no caso mais provável.
    partes.extend(_untracked(workspace, timeout_s=timeout_s))
    # staged e worktree repetem a mesma linha quando nada mudou entre eles
    return "\n".join(dict.fromkeys(partes))


def _untracked(workspace: Path, *, timeout_s: int) -> list[str]:
    from .tests import _e_ruido

    proc = _git(workspace, ["ls-files", "-z", "--others", "--exclude-standard"],
                timeout_s=timeout_s)
    if proc.returncode != 0:
        raise ScannerIndisponivel(f"git ls-files falhou (exit {proc.returncode})")

    saida: list[str] = []
    for rel in proc.stdout.split("\0"):
        if not rel or _e_ruido(rel):
            continue
        alvo = workspace / rel
        try:
            if alvo.is_symlink():
                continue   # o git versiona o texto do link, não o alvo
            if alvo.stat().st_size > 1_000_000:
                continue   # binário/dump: o scanner é de texto
            saida.append(alvo.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise ScannerIndisponivel(
                f"arquivo não versionado ilegível: {type(exc).__name__}") from exc
    return saida


class SecretsGate:
    """Reprova o run que introduziu credencial no código."""

    name = "secrets"
    # Um scanner de segredos quebrado não pode liberar uma execução governada
    # como se tivesse verificado o diff. O custo de parar para revisão humana
    # é menor que o de concluir com uma credencial potencialmente exposta.
    critical = True

    def __init__(self, workspace: "str | Path", *, diff_fn: Any = None,
                 timeout_s: int = 30) -> None:
        self.workspace = Path(workspace)
        self.timeout_s = max(1, int(timeout_s))
        self._diff = diff_fn or (
            lambda ws: linhas_adicionadas(ws, timeout_s=self.timeout_s))

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        try:
            adicionado = self._diff(self.workspace)
        except ScannerIndisponivel as exc:
            # Gate CRÍTICO: não conseguir olhar não é o mesmo que não achar.
            return _GR(self.name, False,
                       f"não foi possível escanear o diff por segredos: {exc}")
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
