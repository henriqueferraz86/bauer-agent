"""Testes da tool sqlite_query.

O valor dela e ser nativa: `sqlite3` vem na stdlib, entao funciona em toda
plataforma sem instalar nada — inclusive Windows com Smart App Control, onde o
binario do MCP Toolbox nem executa. As duas travas abaixo sao o que a torna
segura o bastante para ser `read/low/approval=False`.
"""
from __future__ import annotations

import sqlite3

import pytest

from bauer.tools.base import SandboxError, ToolError
from bauer.tool_router import ToolRouter


@pytest.fixture()
def router_com_banco(tmp_path):
    db = tmp_path / "dados.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE itens (id INTEGER PRIMARY KEY, nome TEXT, valor INTEGER)")
    con.executemany("INSERT INTO itens (nome, valor) VALUES (?, ?)",
                    [("alfa", 1), ("beta", 2), ("gama", 3)])
    con.commit()
    con.close()
    return ToolRouter(workspace=str(tmp_path)), db


def _q(router, **kwargs) -> str:
    return router._tools["sqlite_query"]["fn"](kwargs)


# ─── leitura ─────────────────────────────────────────────────────────────────

def test_select_devolve_linhas_e_colunas(router_com_banco):
    router, db = router_com_banco

    out = _q(router, path=str(db), sql="SELECT nome, valor FROM itens ORDER BY id")

    assert "nome | valor" in out
    assert "alfa | 1" in out and "gama | 3" in out
    assert "3 linha(s)" in out


def test_parametros_sao_vinculados_nao_interpolados(router_com_banco):
    router, db = router_com_banco

    out = _q(router, path=str(db), sql="SELECT nome FROM itens WHERE valor > ?", params=[1])

    assert "beta" in out and "gama" in out
    assert "alfa" not in out


def test_limit_corta_e_avisa(router_com_banco):
    router, db = router_com_banco

    out = _q(router, path=str(db), sql="SELECT * FROM itens", limit=2)

    assert "2 linha(s)" in out
    assert "parou em 2 linhas" in out


def test_select_vazio_nao_e_erro(router_com_banco):
    router, db = router_com_banco

    out = _q(router, path=str(db), sql="SELECT * FROM itens WHERE valor > 999")

    assert "nenhuma linha" in out


# ─── trava 1: o driver recusa escrita ────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "DELETE FROM itens",
    "UPDATE itens SET valor = 0",
    "INSERT INTO itens (nome, valor) VALUES ('x', 9)",
    "DROP TABLE itens",
])
def test_escrita_e_recusada_pelo_driver(router_com_banco, sql):
    """Nao ha parser de SQL procurando UPDATE disfarcado: quem recusa e o
    SQLite, por causa do mode=ro. Vale para qualquer forma de escrita."""
    router, db = router_com_banco

    with pytest.raises(ToolError, match="SOMENTE LEITURA"):
        _q(router, path=str(db), sql=sql)

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM itens").fetchone()[0] == 3
    con.close()


# ─── trava 2: containment de path ────────────────────────────────────────────

def test_banco_fora_do_workspace_e_recusado(tmp_path):
    """Sem isto a tool viraria um jeito de ler qualquer .db da maquina —
    historico e cookies de navegador sao SQLite."""
    fora = tmp_path / "fora"
    fora.mkdir()
    alheio = fora / "cookies.db"
    con = sqlite3.connect(alheio)
    con.execute("CREATE TABLE segredos (v TEXT)")
    con.commit()
    con.close()

    ws = tmp_path / "ws"
    ws.mkdir()
    router = ToolRouter(workspace=str(ws))

    with pytest.raises(SandboxError, match="acesso negado"):
        _q(router, path=str(alheio), sql="SELECT * FROM segredos")


def test_banco_do_bauer_home_e_permitido(tmp_path, monkeypatch):
    home = tmp_path / "bauer_home"
    home.mkdir()
    db = home / "sessions.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('s1')")
    con.commit()
    con.close()

    monkeypatch.setattr("bauer.paths.get_bauer_home", lambda: home)
    ws = tmp_path / "ws"
    ws.mkdir()
    router = ToolRouter(workspace=str(ws))

    out = _q(router, path=str(db), sql="SELECT id FROM sessions")

    assert "s1" in out


# ─── erros com mensagem util ─────────────────────────────────────────────────

def test_arquivo_inexistente(tmp_path):
    router = ToolRouter(workspace=str(tmp_path))

    with pytest.raises(ToolError, match="nao encontrado"):
        _q(router, path=str(tmp_path / "nao_existe.db"), sql="SELECT 1")


def test_sql_invalido_explica(router_com_banco):
    router, db = router_com_banco

    with pytest.raises(ToolError, match="erro de SQL"):
        _q(router, path=str(db), sql="SELECT * FROM tabela_que_nao_existe")


def test_exige_path_e_sql(tmp_path):
    router = ToolRouter(workspace=str(tmp_path))

    with pytest.raises(ToolError, match="requer 'path'"):
        _q(router, sql="SELECT 1")
    with pytest.raises(ToolError, match="requer 'sql'"):
        _q(router, path=str(tmp_path / "x.db"))
