"""Nenhum pacote de código pode ser invisível para o git.

Bug real, pego só ao instalar de verdade: `.gitignore` tinha `workspace/` sem
barra inicial, e regra sem âncora casa com QUALQUER diretório desse nome em
qualquer profundidade — inclusive `bauer/core/workspace/`, que é código.

O efeito é traiçoeiro: os arquivos existem no disco, a suíte local passa (roda do
checkout, não do wheel), `git add -A` não reclama, o commit sai limpo e o CI fica
verde. Só no ambiente instalado aparece `ModuleNotFoundError`.

Este teste fecha a classe inteira, não só o caso do `workspace`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_nenhum_pacote_do_bauer_esta_no_gitignore():
    ignorados = []
    for init in sorted((RAIZ / "bauer").rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        rel = init.relative_to(RAIZ)
        r = subprocess.run(["git", "check-ignore", "-v", str(rel)],
                           cwd=RAIZ, capture_output=True, text=True)
        if r.returncode == 0:
            ignorados.append(f"{rel}  <-  {r.stdout.strip()}")

    assert not ignorados, (
        "Estes pacotes de código são ignorados pelo git e NÃO entram no wheel:\n"
        + "\n".join(f"  {i}" for i in ignorados)
        + "\n\nA causa quase sempre é regra de .gitignore sem barra inicial "
          "(`nome/` casa em qualquer profundidade; `/nome/` só na raiz)."
    )


def test_todo_pacote_do_bauer_esta_rastreado():
    """Existir no disco e estar no .gitignore são coisas diferentes de estar
    COMMITADO — um arquivo novo não-adicionado também sumiria do wheel."""
    nao_rastreados = []
    for init in sorted((RAIZ / "bauer").rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        rel = init.relative_to(RAIZ)
        r = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)],
                           cwd=RAIZ, capture_output=True, text=True)
        if r.returncode != 0:
            nao_rastreados.append(str(rel))

    assert not nao_rastreados, (
        "Pacotes existem no disco mas não estão no git — sumiriam do wheel:\n"
        + "\n".join(f"  {p}" for p in nao_rastreados)
    )
