"""Testes do ToolCallDeduper — replay de tool calls idênticos.

Cenário-origem (bug real 2026-06-10): em bridge mode o modelo repetiu
read_file e execute_code com args idênticos, queimando contexto até a
resposta vazia. O deduper devolve o resultado cacheado com aviso.
"""

from __future__ import annotations

import threading

from bauer.tool_dedup import MUTATING_TOOLS, ToolCallDeduper


class TestDedupBasico:
    def test_primeira_chamada_nao_e_replay(self):
        d = ToolCallDeduper()
        assert d.check("read_file", {"path": "a.py"}) is None

    def test_chamada_identica_e_replay(self):
        d = ToolCallDeduper()
        d.record("read_file", {"path": "a.py"}, "conteudo de a")
        replay = d.check("read_file", {"path": "a.py"})
        assert replay is not None
        assert "conteudo de a" in replay
        assert "[dedup]" in replay
        assert d.replays == 1

    def test_args_diferentes_nao_e_replay(self):
        d = ToolCallDeduper()
        d.record("read_file", {"path": "a.py"}, "conteudo")
        assert d.check("read_file", {"path": "b.py"}) is None

    def test_ordem_de_chaves_nao_importa(self):
        d = ToolCallDeduper()
        d.record("regex_search", {"pattern": "x", "path": "."}, "resultado")
        assert d.check("regex_search", {"path": ".", "pattern": "x"}) is not None

    def test_falha_nao_e_cacheada(self):
        d = ToolCallDeduper()
        d.record("read_file", {"path": "a.py"}, "[Erro: nao existe]", failed=True)
        assert d.check("read_file", {"path": "a.py"}) is None


class TestMutacao:
    def test_tool_mutante_nunca_e_dedupada(self):
        d = ToolCallDeduper()
        d.record("write_file", {"path": "a.py", "content": "x"}, "ok")
        assert d.check("write_file", {"path": "a.py", "content": "x"}) is None

    def test_mutacao_invalida_leituras_anteriores(self):
        d = ToolCallDeduper()
        d.record("read_file", {"path": "a.py"}, "versao antiga")
        d.record("write_file", {"path": "a.py", "content": "novo"}, "ok")
        # read após write deve re-executar (cache invalidado)
        assert d.check("read_file", {"path": "a.py"}) is None

    def test_execute_code_e_replayavel(self):
        """Caso do screenshot: exec(y) → read(x) → exec(y) — 2º exec replaya."""
        d = ToolCallDeduper()
        d.record("execute_code", {"code": "print(1)"}, "1")
        d.record("read_file", {"path": "x.py"}, "conteudo")
        replay = d.check("execute_code", {"code": "print(1)"})
        assert replay is not None
        assert "1" in replay

    def test_execute_code_invalida_leituras_anteriores(self):
        """exec pode ter escrito arquivos via Python — reads antigos caem."""
        d = ToolCallDeduper()
        d.record("read_file", {"path": "x.py"}, "antes")
        d.record("execute_code", {"code": "open('x.py','w').write('depois')"}, "ok")
        assert d.check("read_file", {"path": "x.py"}) is None

    def test_mutating_cobre_tools_de_arquivo(self):
        for tool in ("write_file", "patch", "delete_file", "move_file", "shell"):
            assert tool in MUTATING_TOOLS


class TestLimitesEThreads:
    def test_cache_respeita_max_entries(self):
        d = ToolCallDeduper(max_entries=3)
        for i in range(5):
            d.record("read_file", {"path": f"{i}.py"}, f"c{i}")
        # Os 2 mais antigos caíram
        assert d.check("read_file", {"path": "0.py"}) is None
        assert d.check("read_file", {"path": "1.py"}) is None
        assert d.check("read_file", {"path": "4.py"}) is not None

    def test_clear(self):
        d = ToolCallDeduper()
        d.record("read_file", {"path": "a.py"}, "x")
        d.clear()
        assert d.check("read_file", {"path": "a.py"}) is None

    def test_acesso_concorrente_nao_quebra(self):
        d = ToolCallDeduper()
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(50):
                    d.record("read_file", {"path": f"{n}-{i}.py"}, "c")
                    d.check("read_file", {"path": f"{n}-{i}.py"})
                    if i % 10 == 0:
                        d.record("write_file", {"path": "w.py"}, "ok")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_args_nao_serializaveis_nao_quebram(self):
        d = ToolCallDeduper()
        unserializable = {"obj": object()}
        d.record("read_file", unserializable, "x")
        assert d.check("read_file", unserializable) is not None
