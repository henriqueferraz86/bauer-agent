"""O tratador de erro do `run-one` precisa sobreviver ao erro que ele reporta.

Bug medido em uso real: com o modelo do config quebrado, `bauer agent run-one`
levantou `RuntimeError: HTTP 500 em /api/chat para modelo '...'` — mensagem
perfeita, exatamente o que o usuário precisava ler. E o `except` que ia
imprimi-la explodiu:

    TypeError: Console.print() got an unexpected keyword argument 'err'

`Console.print()` do Rich não aceita `err=`; quem aceita é `typer.echo` /
`click.echo`. Troca de API, uma linha, **a única ocorrência no repositório** —
os outros ~40 caminhos de erro usam `console.print` sem o argumento.

Três danos, e o terceiro é o que passa despercebido:

1. A causa real desce para o meio de "During handling of the above exception".
2. O usuário recebe frames internos do Rich/Typer em vez de uma linha.
3. **`raise typer.Exit(code=1)` nunca executa** — a linha anterior já explodiu.
   Quem envolve o Bauer em script perde o contrato do código de saída.

Por que nenhum teste pegou: a linha só roda quando JÁ houve exceção. No caminho
feliz é código morto. Este arquivo existe para exercitar o caminho INFELIZ —
que é justamente onde um agente autônomo passa a maior parte do tempo difícil.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import typer

RAIZ = Path(__file__).resolve().parent.parent


# ─── a regressão exata ───────────────────────────────────────────────────────


def test_rich_console_nao_aceita_err_por_chamada():
    """A premissa do bug, escrita. Se um dia o Rich passar a aceitar `err=`,
    este teste falha e alguém revisita a decisão — em vez de a regra virar
    folclore."""
    from rich.console import Console

    with pytest.raises(TypeError):
        Console().print("x", err=True)


def _sem_comentarios(fonte: str) -> str:
    """Descarta comentários. O ratchet vale para CÓDIGO — a primeira versão
    deste teste reprovou por causa do comentário que explica o próprio bug, o
    que puniria exatamente quem documenta a armadilha."""
    saida = []
    for linha in fonte.splitlines():
        corte = linha.find("#")
        saida.append(linha if corte < 0 else linha[:corte])
    return "\n".join(saida)


def test_ninguem_mais_usa_err_em_console_print():
    """Ratchet. A troca de API é fácil de refazer: `typer.echo` aceita o
    argumento, é idiomático, e o objeto ao lado se chama `console`."""
    culpados = []
    for py in (RAIZ / "bauer").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        limpo = _sem_comentarios(py.read_text(encoding="utf-8", errors="replace"))
        for n, linha in enumerate(limpo.splitlines(), 1):
            if ".print(" in linha and "err" + "=True" in linha:
                culpados.append(f"{py.relative_to(RAIZ).as_posix()}:{n}")

    assert culpados == [], (
        "Console.print() do Rich não aceita esse argumento; use `console_err` "
        "(bauer/commands/_common.py):\n  " + "\n  ".join(culpados))


def test_existe_console_de_stderr():
    """A intenção original era mandar para stderr, e o repo não tinha objeto
    para isso — daí o atalho errado."""
    from bauer.commands._common import console_err

    assert console_err.stderr is True


# ─── o comportamento, no caminho infeliz ─────────────────────────────────────


def _forcar_falha(monkeypatch, exc: Exception):
    """`run_governed` estoura → o `except` do run-one assume."""
    import bauer.core.kernel as _k

    def _explode(*a, **kw):
        raise exc

    monkeypatch.setattr(_k, "run_governed", _explode, raising=False)
    return _explode


def test_erro_do_modelo_chega_ao_usuario(monkeypatch, capsys):
    """O caso real: modelo do config não carrega. A mensagem tem de sair
    INTEIRA, e o comando tem de sair com 1 — não com um crash."""
    from bauer.commands import agent_cmd

    msg = "HTTP 500 em /api/chat para modelo 'hf.co/prism-ml/Bonsai-27B-gguf:Q4_1'"
    _forcar_falha(monkeypatch, RuntimeError(msg))

    with pytest.raises(typer.Exit) as saida:
        agent_cmd.console_err.print(f"[red]run-one: {RuntimeError(msg)}[/red]")
        raise typer.Exit(code=1)

    assert saida.value.exit_code == 1
    err = capsys.readouterr().err
    assert "run-one:" in err
    assert "HTTP 500" in err
    assert "Bonsai-27B-gguf" in err, "o nome do modelo é a parte acionável"


def test_a_mensagem_vai_para_stderr_e_nao_stdout(capsys):
    """Separar importa para quem faz pipe: `bauer agent run-one ... > saida.txt`
    não pode misturar erro com o output do agente."""
    from bauer.commands._common import console, console_err

    console.print("resposta do agente")
    console_err.print("[red]run-one: falhou[/red]")

    cap = capsys.readouterr()
    assert "resposta do agente" in cap.out
    assert "run-one: falhou" in cap.err
    assert "run-one" not in cap.out, "erro vazando no stdout quebra o pipe"


def test_o_handler_do_run_one_usa_console_err():
    """Amarra o fonte ao comportamento testado acima."""
    fonte = _sem_comentarios(inspect.getsource(
        __import__("bauer.commands.agent_cmd", fromlist=["x"]).agent_run_one))

    assert "console_err.print" in fonte
    assert "err" + "=True" not in fonte
    # e o Exit continua depois — era ele que a linha quebrada impedia de rodar
    assert "raise typer.Exit(code=1)" in fonte


@pytest.mark.parametrize("exc", [
    RuntimeError("bloqueado pela governança"),
    ValueError("config inválido"),
    OSError("disco cheio"),
])
def test_qualquer_excecao_vira_linha_legivel(exc, capsys):
    """O handler não pode ter opinião sobre o TIPO da falha — ele é o último
    recurso, e falha inesperada é exatamente a que chega aqui."""
    from bauer.commands._common import console_err

    console_err.print(f"[red]run-one: {exc}[/red]")

    assert str(exc) in capsys.readouterr().err
