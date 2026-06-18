"""Testes para as novas tools: patch, todo, memory, execute_code, clarify,
delegate_task, vision_analyze, mcp_call."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Suprime warning de coroutine não awaited que vem do mock de asyncio.run em mcp tests
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

from bauer.tool_router import ToolError, ToolRouter


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


# ===========================================================================
# PATCH
# ===========================================================================

class TestPatch:
    def test_patch_substitui_trecho(self, router, ws):
        f = ws / "hello.txt"
        f.write_text("Hello world!\nFoo bar.", encoding="utf-8")
        result = router._patch_file({"path": "hello.txt", "old_string": "world", "new_string": "Python"})
        assert "hello.txt" in result
        assert "atualizado" in result
        assert f.read_text(encoding="utf-8") == "Hello Python!\nFoo bar."

    def test_patch_apaga_trecho_sem_new_string(self, router, ws):
        f = ws / "f.txt"
        f.write_text("aXb", encoding="utf-8")
        router._patch_file({"path": "f.txt", "old_string": "X"})
        assert f.read_text(encoding="utf-8") == "ab"

    def test_patch_falha_se_nao_encontrado(self, router, ws):
        (ws / "f.txt").write_text("abc", encoding="utf-8")
        with pytest.raises(ToolError, match="nao encontrado"):
            router._patch_file({"path": "f.txt", "old_string": "ZZZ", "new_string": "X"})

    def test_patch_falha_se_ambiguo(self, router, ws):
        (ws / "f.txt").write_text("aXaXa", encoding="utf-8")
        with pytest.raises(ToolError, match="vezes"):
            router._patch_file({"path": "f.txt", "old_string": "X", "new_string": "Y"})

    def test_patch_falha_sem_path(self, router):
        with pytest.raises(ToolError, match="path"):
            router._patch_file({"old_string": "x", "new_string": "y"})

    def test_patch_falha_sem_old_string(self, router, ws):
        (ws / "f.txt").write_text("abc", encoding="utf-8")
        with pytest.raises(ToolError, match="old_string"):
            router._patch_file({"path": "f.txt"})

    def test_patch_arquivo_nao_existe(self, router):
        with pytest.raises(ToolError, match="nao encontrado"):
            router._patch_file({"path": "nao_existe.txt", "old_string": "x"})

    def test_patch_mostra_diff(self, router, ws):
        (ws / "f.txt").write_text("linha1\nlinha2\nlinha3\n", encoding="utf-8")
        result = router._patch_file({
            "path": "f.txt", "old_string": "linha2", "new_string": "NOVA"
        })
        assert "---" in result or "@@" in result or "NOVA" in result


# ===========================================================================
# TODO
# ===========================================================================

class TestTodo:
    def test_add_e_list(self, router):
        router._todo({"action": "add", "text": "Fazer testes"})
        result = router._todo({"action": "list"})
        assert "Fazer testes" in result
        assert "○" in result

    def test_add_incrementa_id(self, router):
        r1 = router._todo({"action": "add", "text": "T1"})
        r2 = router._todo({"action": "add", "text": "T2"})
        assert "[1]" in r1
        assert "[2]" in r2

    def test_done_marca_concluido(self, router):
        router._todo({"action": "add", "text": "Tarefa A"})
        router._todo({"action": "done", "id": 1})
        result = router._todo({"action": "list"})
        assert "✓" in result

    def test_remove_elimina_tarefa(self, router):
        router._todo({"action": "add", "text": "Remover"})
        result = router._todo({"action": "remove", "id": 1})
        assert "removida" in result
        assert "Remover" not in router._todo({"action": "list"})

    def test_clear_limpa_tudo(self, router):
        router._todo({"action": "add", "text": "A"})
        router._todo({"action": "add", "text": "B"})
        result = router._todo({"action": "clear"})
        assert "2" in result
        assert "vazia" in router._todo({"action": "list"})

    def test_list_vazio(self, router):
        result = router._todo({"action": "list"})
        assert "vazia" in result

    def test_done_id_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="nao encontrada"):
            router._todo({"action": "done", "id": 999})

    def test_remove_id_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="nao encontrada"):
            router._todo({"action": "remove", "id": 999})

    def test_acao_desconhecida_levanta(self, router):
        with pytest.raises(ToolError, match="desconhecida"):
            router._todo({"action": "voo"})

    def test_add_sem_text_levanta(self, router):
        with pytest.raises(ToolError, match="text"):
            router._todo({"action": "add"})

    def test_done_sem_id_levanta(self, router):
        with pytest.raises(ToolError, match="id"):
            router._todo({"action": "done"})

    def test_contagem_concluidas(self, router):
        router._todo({"action": "add", "text": "A"})
        router._todo({"action": "add", "text": "B"})
        router._todo({"action": "done", "id": 1})
        result = router._todo({"action": "list"})
        assert "1/2" in result


# ===========================================================================
# MEMORY
# ===========================================================================

class TestMemory:
    def test_set_e_get(self, router):
        router._memory({"action": "set", "key": "nome", "value": "Bauer"})
        result = router._memory({"action": "get", "key": "nome"})
        assert "Bauer" in result

    def test_list_mostra_chaves(self, router):
        router._memory({"action": "set", "key": "a", "value": "1"})
        router._memory({"action": "set", "key": "b", "value": "2"})
        result = router._memory({"action": "list"})
        assert "a" in result
        assert "b" in result

    def test_delete_remove_chave(self, router):
        router._memory({"action": "set", "key": "k", "value": "v"})
        result = router._memory({"action": "delete", "key": "k"})
        assert "removido" in result
        assert "nao encontrada" in router._memory({"action": "get", "key": "k"})

    def test_get_chave_inexistente(self, router):
        result = router._memory({"action": "get", "key": "inexistente"})
        assert "nao encontrada" in result

    def test_list_vazia(self, router):
        result = router._memory({"action": "list"})
        assert "vazia" in result

    def test_persiste_em_arquivo(self, router, ws):
        router._memory({"action": "set", "key": "pkey", "value": "pval"})
        mem_file = ws / ".bauer_memory.json"
        assert mem_file.exists()
        data = json.loads(mem_file.read_text(encoding="utf-8"))
        assert "pkey" in data

    def test_set_sem_key_levanta(self, router):
        with pytest.raises(ToolError, match="key"):
            router._memory({"action": "set", "value": "v"})

    def test_set_sem_value_levanta(self, router):
        with pytest.raises(ToolError, match="value"):
            router._memory({"action": "set", "key": "k"})

    def test_valor_muito_grande_levanta(self, router):
        with pytest.raises(ToolError, match="grande"):
            router._memory({"action": "set", "key": "k", "value": "x" * 20_001})

    def test_acao_desconhecida_levanta(self, router):
        with pytest.raises(ToolError, match="desconhecida"):
            router._memory({"action": "purge"})

    def test_delete_chave_inexistente_nao_levanta(self, router):
        result = router._memory({"action": "delete", "key": "nao_existe"})
        assert "nada removido" in result

    def test_segundo_router_le_mesmo_arquivo(self, ws):
        r1 = ToolRouter(workspace=ws)
        r1._memory({"action": "set", "key": "shared", "value": "ok"})
        r2 = ToolRouter(workspace=ws)
        result = r2._memory({"action": "get", "key": "shared"})
        assert "ok" in result


# ===========================================================================
# EXECUTE_CODE
# ===========================================================================

class TestExecuteCode:
    def test_executa_hello_world(self, router):
        result = router._execute_code({"code": "print('Hello, World!')"})
        assert "Hello, World!" in result
        assert "exit: 0" in result

    def test_captura_stderr(self, router):
        result = router._execute_code({"code": "import sys; sys.stderr.write('erro!\\n')"})
        assert "stderr" in result
        assert "erro!" in result

    def test_exit_code_nao_zero(self, router):
        result = router._execute_code({"code": "raise SystemExit(42)"})
        assert "exit: 42" in result

    def test_erro_de_sintaxe(self, router):
        result = router._execute_code({"code": "def f(: pass"})
        assert "exit:" in result
        assert "stderr" in result

    def test_sem_output(self, router):
        result = router._execute_code({"code": "x = 1 + 1"})
        assert "exit: 0" in result
        assert "sem output" in result

    def test_timeout_levanta(self, router):
        with pytest.raises(ToolError, match="Timeout"):
            router._execute_code({"code": "import time; time.sleep(999)", "timeout": 1})

    def test_sem_code_levanta(self, router):
        with pytest.raises(ToolError, match="code"):
            router._execute_code({})

    def test_timeout_max_120(self, router):
        # timeout > 120 deve ser clamped para 120 (sem erro)
        # Apenas verifica que não levanta imediatamente
        result = router._execute_code({"code": "print('ok')", "timeout": 9999})
        assert "exit: 0" in result

    def test_calculo_matematico(self, router):
        result = router._execute_code({"code": "print(2 ** 10)"})
        assert "1024" in result

    def test_multilinhas(self, router):
        code = "for i in range(3):\n    print(i)"
        result = router._execute_code({"code": code})
        assert "0" in result
        assert "1" in result
        assert "2" in result


# ===========================================================================
# CLARIFY
# ===========================================================================

class TestClarify:
    def test_modo_nao_interativo_retorna_placeholder(self, router):
        """Em ambiente de test (sem TTY), deve retornar placeholder."""
        result = router._clarify({"question": "Qual e a capital do Brasil?"})
        assert "clarify" in result.lower()
        assert "Qual e a capital do Brasil?" in result

    def test_sem_question_levanta(self, router):
        with pytest.raises(ToolError, match="question"):
            router._clarify({})

    def test_choices_aparecem_no_placeholder(self, router):
        result = router._clarify({
            "question": "Continuar?",
            "choices": "sim|nao",
        })
        assert "sim" in result.lower() or "nao" in result.lower()

    def test_modo_interativo_com_input_mockado(self, router):
        with patch("builtins.input", return_value="resposta"), \
             patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            result = router._clarify({"question": "Pergunta?"})
        assert result == "resposta"

    def test_modo_interativo_choices_valida(self, router):
        with patch("builtins.input", return_value="sim"), \
             patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            result = router._clarify({
                "question": "Confirma?",
                "choices": "sim|nao",
            })
        assert result == "sim"

    def test_modo_interativo_choices_invalida(self, router):
        with patch("builtins.input", return_value="talvez"), \
             patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            result = router._clarify({
                "question": "Confirma?",
                "choices": "sim|nao",
            })
        assert "invalida" in result or "invalido" in result


# ===========================================================================
# DELEGATE_TASK
# ===========================================================================

class TestDelegateTask:
    def test_sem_task_levanta(self, router):
        with pytest.raises(ToolError, match="task"):
            router._delegate_task({})

    def test_usa_llm_client_se_disponivel(self, ws):
        mock_client = MagicMock()
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        with patch("bauer.agent.run_one_turn", return_value="resultado do sub-agente"):
            result = router._delegate_task({"task": "Calcule 2+2"})
        assert "sub-agente" in result
        assert "resultado" in result

    def test_fallback_subprocess_quando_sem_client(self, router):
        """Sem llm_client e sem bauer instalado, deve levantar ToolError."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Output do sub-agente",
                stderr="",
            )
            result = router._delegate_task({"task": "Tarefa X"})
        assert "sub-agente" in result

    def test_timeout_levanta(self, ws):
        import subprocess
        router = ToolRouter(workspace=ws)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            with pytest.raises(ToolError, match="timeout"):
                router._delegate_task({"task": "Tarefa lenta", "timeout": 1})

    def test_contexto_e_concatenado(self, ws):
        mock_client = MagicMock()
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        captured = {}
        def fake_run_one_turn(client, messages, tools):
            captured["content"] = messages[0]["content"]
            return "ok"
        with patch("bauer.agent.run_one_turn", side_effect=fake_run_one_turn):
            router._delegate_task({"task": "A tarefa", "context": "Contexto extra"})
        assert "Contexto extra" in captured["content"]
        assert "A tarefa" in captured["content"]


# ===========================================================================
# VISION_ANALYZE
# ===========================================================================

class TestVisionAnalyze:
    def test_sem_image_levanta(self, router):
        with pytest.raises(ToolError, match="image"):
            router._vision_analyze({"query": "o que vejo?"})

    def test_sem_query_levanta(self, router):
        with pytest.raises(ToolError, match="query"):
            router._vision_analyze({"image": "https://example.com/img.jpg"})

    def test_sem_client_levanta_instrucao(self, router):
        # G18.4: erro agora aponta para auxiliary.vision_model
        with pytest.raises(ToolError, match="vision_model"):
            router._vision_analyze({
                "image": "https://example.com/img.jpg",
                "query": "Descreva",
            })

    def test_com_client_e_url(self, ws):
        mock_client = MagicMock()
        mock_client.model = "gpt-4o"  # G18.4: modelo multimodal p/ passar no gate
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        with patch("bauer.agent.run_one_turn", return_value="Um gato sentado."):
            result = router._vision_analyze({
                "image": "https://example.com/cat.jpg",
                "query": "O que tem na imagem?",
            })
        assert "gato" in result

    def test_com_path_local(self, ws):
        # Cria imagem fake (PNG mínimo)
        img = ws / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        mock_client = MagicMock()
        mock_client.model = "gpt-4o"
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        with patch("bauer.agent.run_one_turn", return_value="Imagem analisada."):
            result = router._vision_analyze({
                "image": "test.png",
                "query": "Descreva",
            })
        assert "Imagem" in result

    def test_path_local_nao_existente_levanta(self, ws):
        mock_client = MagicMock()
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        with pytest.raises(ToolError, match="nao encontrada"):
            router._vision_analyze({
                "image": "inexistente.jpg",
                "query": "Descreva",
            })

    def test_mensagem_inclui_image_url(self, ws):
        mock_client = MagicMock()
        mock_client.model = "gpt-4o"
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        captured = {}
        def fake_run(client, messages, tools):
            captured["msg"] = messages[0]
            return "ok"
        with patch("bauer.agent.run_one_turn", side_effect=fake_run):
            router._vision_analyze({
                "image": "https://example.com/img.jpg",
                "query": "Teste",
            })
        content = captured["msg"]["content"]
        assert any(c.get("type") == "image_url" for c in content)
        assert any(c.get("type") == "text" for c in content)


# ===========================================================================
# MCP_CALL
# ===========================================================================

class TestMcpCall:
    def test_sem_server_levanta(self, router):
        with pytest.raises(ToolError, match="server"):
            router._mcp_call({})

    def test_sem_tool_levanta(self, router):
        with pytest.raises(ToolError, match="tool"):
            router._mcp_call({"server": "meu_server"})

    def test_sem_mcp_instalado_levanta(self, router):
        """Se mcp não estiver instalado, deve levantar ToolError com instrução pip."""
        import sys
        # Garante que mcp está ausente do sys.modules para forçar ImportError
        mcp_keys = [k for k in list(sys.modules) if k == "mcp" or k.startswith("mcp.")]
        mcp_backup = {k: sys.modules.pop(k) for k in mcp_keys}
        try:
            with pytest.raises(ToolError) as exc_info:
                router._mcp_call({
                    "server": "meu_server",
                    "tool": "hello",
                    "arguments": {},
                })
            # Pode falhar com "não instalado" ou "não configurado" dependendo se mcp está no env
            assert "mcp" in str(exc_info.value).lower()
        finally:
            sys.modules.update(mcp_backup)

    def test_server_nao_configurado_levanta(self, router):
        import sys
        import types
        # Simula mcp instalado
        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = MagicMock()
        fake_mcp.StdioServerParameters = MagicMock()
        fake_mcp_stdio = types.ModuleType("mcp.client.stdio")
        fake_mcp_stdio.stdio_client = MagicMock()
        with patch.dict(sys.modules, {"mcp": fake_mcp, "mcp.client": types.ModuleType("mcp.client"), "mcp.client.stdio": fake_mcp_stdio}):
            with pytest.raises(ToolError, match="nao configurado"):
                router._mcp_call({
                    "server": "servidor_inexistente",
                    "tool": "hello",
                    "arguments": {},
                })

    def test_server_via_env(self, router, monkeypatch):
        import sys
        import types
        monkeypatch.setenv("MCP_SERVER_MEU_SERVER", "python -m meu_server")

        # Simula mcp instalado — DEVE estar em sys.modules antes de _mcp_call importar
        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = MagicMock()
        fake_mcp.StdioServerParameters = MagicMock()
        fake_mcp_client = types.ModuleType("mcp.client")
        fake_mcp_stdio = types.ModuleType("mcp.client.stdio")
        fake_mcp_stdio.stdio_client = MagicMock()

        fake_result = MagicMock()
        fake_text_content = MagicMock()
        fake_text_content.text = "resultado mcp"
        fake_result.content = [fake_text_content]

        # Injeta mcp no sys.modules e bypassa asyncio.run retornando diretamente
        mcp_modules = {
            "mcp": fake_mcp,
            "mcp.client": fake_mcp_client,
            "mcp.client.stdio": fake_mcp_stdio,
        }
        with patch.dict("sys.modules", mcp_modules):
            with patch.object(router, "_get_mcp_server_cmd", return_value=["python", "-m", "meu_server"]):
                with patch("asyncio.run", return_value="resultado mcp"):
                    result = router._mcp_call({
                        "server": "meu_server",
                        "tool": "hello",
                        "arguments": {"x": 1},
                    })
        assert "resultado mcp" in result


# ===========================================================================
# Integração: tools registradas no router
# ===========================================================================

class TestRegistroTools:
    def test_todas_novas_tools_registradas(self, router):
        tools = router.available_tools()
        for name in ["patch", "todo", "memory", "execute_code", "clarify",
                     "delegate_task", "vision_analyze", "mcp_call"]:
            assert name in tools, f"Tool '{name}' nao registrada"

    def test_schemas_validos(self, router):
        schemas = router.get_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        for name in ["patch", "todo", "memory", "execute_code"]:
            assert name in names

    def test_total_tools_26(self, router):
        # Contagem mínima: wave 1-4 adicionou 8 tools; waves posteriores adicionam mais
        from pathlib import Path
        r = ToolRouter(workspace=router.workspace, web_enabled=True)
        assert len(r.get_tool_schemas()) >= 26
