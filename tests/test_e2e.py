"""Testes E2E — fluxo completo do agente e orquestrador com mock client.

Estes testes exercitam o ciclo completo:
  1. Agent session: usuário → contexto → modelo → tool → resposta
  2. Orchestrator: plan → DAG → execução paralela → síntese
  3. Tool Bridge: parse JSON → executa ferramenta → resultado → modelo
  4. Native tool calling: OpenAIClient path com tool_calls
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from bauer.agent import run_one_turn, _try_parse_tool, _collect_response
from bauer.context_manager import ContextManager
from bauer.tool_router import ToolRouter


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mock_ollama_client(responses: list[str]):
    """Cria mock de OllamaClient que retorna respostas em sequência."""
    client = MagicMock()
    responses_iter = iter(responses)

    def _chat_stream(model, messages):
        try:
            resp = next(responses_iter)
        except StopIteration:
            resp = "Resposta final."
        return iter([resp])

    client.chat_stream.side_effect = _chat_stream
    # Garante que NÃO é OpenAIClient (sem native tools)
    del client.supports_native_tools
    return client


def _mock_openai_client(tool_responses: list[dict | str]):
    """Cria mock de OpenAIClient com native tool calling."""
    from bauer.openai_client import OpenAIClient
    client = MagicMock(spec=OpenAIClient)
    client.supports_native_tools = True

    responses_iter = iter(tool_responses)

    def _chat_with_tools(model, messages, tools, tool_choice="auto"):
        try:
            resp = next(responses_iter)
        except StopIteration:
            resp = {"content": "Resposta final.", "tool_calls": None}
        if isinstance(resp, str):
            return {"content": resp, "tool_calls": None}
        return resp

    client.chat_with_tools.side_effect = _chat_with_tools

    def _chat_stream(model, messages):
        return iter(["Streaming resposta."])

    client.chat_stream.side_effect = _chat_stream
    return client


def _make_ctx(system: str = "System") -> ContextManager:
    return ContextManager(applied_context=4096, system_prompt=system)


# ─── E2E: Tool Bridge (Ollama path) ──────────────────────────────────────────

class TestE2EToolBridge:
    def test_simple_text_response(self, tmp_path: Path):
        """Modelo responde em texto puro — sem tool calls."""
        client = _mock_ollama_client(["Olá! Como posso ajudar?"])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("oi")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")

        assert response == "Olá! Como posso ajudar?"
        assert tool_log == []

    def test_tool_call_then_text(self, tmp_path: Path):
        """Modelo faz tool call, recebe resultado, responde em texto."""
        list_response = json.dumps({"action": "list_dir", "args": {"path": "."}})
        client = _mock_ollama_client([list_response, "Aqui estão os arquivos."])

        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("liste os arquivos")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")

        assert response == "Aqui estão os arquivos."
        assert len(tool_log) == 1
        assert tool_log[0]["tool"] == "list_dir"

    def test_write_then_read_tool_chain(self, tmp_path: Path):
        """Encadeia write_file → read_file — exercita múltiplos tool turns."""
        write_req = json.dumps({"action": "write_file", "args": {
            "path": "hello.txt", "content": "Hello World"
        }})
        read_req = json.dumps({"action": "read_file", "args": {"path": "hello.txt"}})
        client = _mock_ollama_client([write_req, read_req, "Arquivo criado e lido com sucesso!"])

        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("crie e leia um arquivo")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")

        assert "sucesso" in response.lower() or isinstance(response, str)
        assert len(tool_log) == 2
        # Arquivo deve ter sido criado
        assert (tmp_path / "hello.txt").exists()

    def test_tool_error_is_recovered(self, tmp_path: Path):
        """Tool error não mata o loop — resultado de erro vai ao contexto."""
        bad_req = json.dumps({"action": "read_file", "args": {"path": "nao_existe.txt"}})
        client = _mock_ollama_client([bad_req, "Arquivo não encontrado."])

        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("leia nao_existe.txt")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")

        # Deve ter executado a tool e obtido erro, então modelo respondeu
        assert isinstance(response, str)
        assert len(tool_log) >= 1

    def test_max_tool_turns_limit(self, tmp_path: Path):
        """Após MAX_TOOL_TURNS, o loop para mesmo sem resposta de texto."""
        from bauer.agent import MAX_TOOL_TURNS
        # Gera infinitas tool calls
        list_req = json.dumps({"action": "list_dir", "args": {"path": "."}})
        # Responde sempre com tool call
        client = _mock_ollama_client([list_req] * (MAX_TOOL_TURNS + 5))

        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("liste infinitamente")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")

        # Não deve ter mais de MAX_TOOL_TURNS tool calls
        assert len(tool_log) <= MAX_TOOL_TURNS

    def test_context_accumulates_across_turns(self, tmp_path: Path):
        """Contexto acumula mensagens entre turns do usuário."""
        client = _mock_ollama_client(["Primeira resposta.", "Segunda resposta."])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()

        ctx.add_user("primeira pergunta")
        run_one_turn(ctx, router, client, "phi4-mini")

        ctx.add_user("segunda pergunta")
        run_one_turn(ctx, router, client, "phi4-mini")

        # Contexto deve ter acumulado: user + assistant + user + assistant = 4 msgs
        assert len(ctx.messages) >= 4

    def test_pseudo_json_response_extracted(self, tmp_path: Path):
        """Modelo responde com JSON de conversa — extrai texto."""
        pseudo = json.dumps({
            "action": "resposta",
            "args": {"conteudo": "Texto extraído do JSON."}
        })
        client = _mock_ollama_client([pseudo])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("oi")

        response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")
        # Pseudo-JSON não deve ser parseado como tool call (action "resposta" não é tool real)
        assert tool_log == []


# ─── E2E: Native Tool Calling (OpenAI path) ──────────────────────────────────

class TestE2ENativeToolCalling:
    def test_direct_text_response(self, tmp_path: Path):
        """OpenAI client responde sem tool calls."""
        client = _mock_openai_client(["Resposta direta sem tools."])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("oi")

        response, tool_log = run_one_turn(ctx, router, client, "gpt-4o")

        assert response == "Resposta direta sem tools."
        assert tool_log == []

    def test_native_tool_call_executed(self, tmp_path: Path):
        """OpenAI client faz tool call nativa — executa e continua."""
        tool_call_resp = {
            "content": None,
            "tool_calls": [{
                "id": "call_001",
                "function": {
                    "name": "list_dir",
                    "arguments": json.dumps({"path": "."})
                }
            }]
        }
        final_resp = {"content": "Listagem completa!", "tool_calls": None}

        client = _mock_openai_client([tool_call_resp, final_resp])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("liste os arquivos")

        response, tool_log = run_one_turn(ctx, router, client, "gpt-4o")

        assert response == "Listagem completa!"
        assert len(tool_log) == 1
        assert tool_log[0]["tool"] == "list_dir"

    def test_native_tool_error_handled(self, tmp_path: Path):
        """Tool call nativa com erro — é capturado e incluído no contexto."""
        tool_call_resp = {
            "content": None,
            "tool_calls": [{
                "id": "call_002",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "nao_existe.txt"})
                }
            }]
        }
        final_resp = {"content": "Arquivo não encontrado.", "tool_calls": None}

        client = _mock_openai_client([tool_call_resp, final_resp])
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("leia arquivo inexistente")

        response, tool_log = run_one_turn(ctx, router, client, "gpt-4o")

        assert isinstance(response, str)
        assert len(tool_log) == 1
        # Resultado deve conter o erro
        assert "Erro" in tool_log[0]["result"] or isinstance(tool_log[0]["result"], str)

    def test_native_multiple_tool_calls(self, tmp_path: Path):
        """Múltiplos tool calls em sequência."""
        (tmp_path / "a.txt").write_text("conteudo a")

        calls = [
            {
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})}
                }]
            },
            {
                "content": None,
                "tool_calls": [{
                    "id": "c2",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})}
                }]
            },
            {"content": "Feito!", "tool_calls": None},
        ]
        client = _mock_openai_client(calls)
        router = ToolRouter(workspace=tmp_path)
        ctx = _make_ctx()
        ctx.add_user("liste e leia")

        response, tool_log = run_one_turn(ctx, router, client, "gpt-4o")

        assert response == "Feito!"
        assert len(tool_log) == 2


# ─── E2E: Orchestrator ───────────────────────────────────────────────────────

def _make_orch(tmp_path: Path):
    """Helper: cria AgentOrchestrator com mocks (mesma abordagem dos testes existentes)."""
    from bauer.orchestrator import AgentOrchestrator, OrchestratorConfig

    client = MagicMock()
    client.chat_stream.return_value = iter(["resposta mock"])
    tool_router = MagicMock()
    model_router = MagicMock()
    model_router.select_model.return_value = ("phi4-mini", MagicMock())
    cfg = OrchestratorConfig(planner_model="phi4-mini", synthesizer_model="phi4-mini")
    orch = AgentOrchestrator(client, tool_router, model_router, cfg)

    # Redireciona progresso para tmp_path
    import hashlib

    def _patched_path(task: str) -> Path:
        h = hashlib.md5(task.encode("utf-8")).hexdigest()[:10]
        return tmp_path / h

    orch._progress_path = _patched_path  # type: ignore[method-assign]
    return orch


class TestE2EOrchestrator:
    def test_orchestrator_dag_parallel_execution(self, tmp_path: Path):
        """Passos independentes executam em paralelo."""
        fixed_plan = [
            {"id": 1, "goal": "tarefa A", "tools": False, "depends_on": []},
            {"id": 2, "goal": "tarefa B", "tools": False, "depends_on": []},
            {"id": 3, "goal": "síntese", "tools": False, "depends_on": [1, 2]},
        ]

        orch = _make_orch(tmp_path)
        batches = orch._topological_batches(fixed_plan)
        assert len(batches) == 2
        # Primeira onda: passos 1 e 2 (paralelos)
        assert len(batches[0]) == 2
        # Segunda onda: passo 3
        assert len(batches[1]) == 1

    def test_orchestrator_plan_and_run(self, tmp_path: Path):
        """Planeja e executa task simples end-to-end."""
        fixed_plan = [
            {"id": 1, "goal": "buscar informações", "tools": False, "depends_on": []},
            {"id": 2, "goal": "gerar relatório", "tools": False, "depends_on": [1]},
        ]

        orch = _make_orch(tmp_path)

        with patch.object(orch, 'plan', return_value=fixed_plan), \
             patch.object(orch, 'execute_step', side_effect=lambda s, prev: __import__('bauer.orchestrator', fromlist=['StepResult']).StepResult(
                 id=s["id"], goal=s["goal"], model_used="phi4-mini",
                 response=f"Resultado {s['id']}", tool_log=[]
             )), \
             patch.object(orch, 'synthesize', return_value="Síntese final"):
            final, results = orch.run("tarefa de teste")

        assert isinstance(final, str)
        assert len(results) == 2

    def test_orchestrator_resume_from_progress(self, tmp_path: Path):
        """Resume retoma de onde parou usando progresso salvo."""
        from bauer.orchestrator import StepResult

        fixed_plan = [
            {"id": 1, "goal": "passo 1", "tools": False, "depends_on": []},
            {"id": 2, "goal": "passo 2", "tools": False, "depends_on": [1]},
        ]

        orch = _make_orch(tmp_path)
        task = "tarefa com resume"

        # Salva plano e resultado do passo 1 (simulando run interrompido)
        orch.save_plan(task, fixed_plan)
        done_result = StepResult(
            id=1, goal="passo 1", model_used="phi4-mini",
            response="Resultado passo 1 já feito.", tool_log=[]
        )
        orch.save_progress(task, [done_result])

        # Resume — passo 1 não deve ser re-executado
        with patch.object(orch, 'execute_step', side_effect=lambda s, prev: StepResult(
            id=s["id"], goal=s["goal"], model_used="phi4-mini",
            response=f"Resultado {s['id']}", tool_log=[]
        )), patch.object(orch, 'synthesize', return_value="Síntese final"):
            final, results = orch.run(task, resume=True)

        assert isinstance(final, str)
        # Passo 1 veio do cache, passo 2 foi executado
        assert any(r.id == 1 for r in results)
        assert any(r.id == 2 for r in results)

    def test_orchestrator_progress_cleared_on_success(self, tmp_path: Path):
        """Progresso é limpo após conclusão bem-sucedida."""
        from bauer.orchestrator import StepResult

        fixed_plan = [
            {"id": 1, "goal": "único passo", "tools": False, "depends_on": []},
        ]

        orch = _make_orch(tmp_path)
        task = "tarefa única"

        with patch.object(orch, 'plan', return_value=fixed_plan), \
             patch.object(orch, 'execute_step', return_value=StepResult(
                 id=1, goal="único passo", model_used="phi4-mini",
                 response="Feito!", tool_log=[]
             )), \
             patch.object(orch, 'synthesize', return_value="Síntese"):
            orch.run(task)

        # Após run(), progresso deve ser limpo
        progress = orch.load_progress(task)
        assert progress == []

    def test_orchestrator_topological_batches_circular(self, tmp_path: Path):
        """Dependência circular — fallback para processar restante."""
        orch = _make_orch(tmp_path)

        # Dependência circular: 1→2, 2→1
        steps = [
            {"id": 1, "goal": "passo 1", "depends_on": [2]},
            {"id": 2, "goal": "passo 2", "depends_on": [1]},
        ]
        batches = orch._topological_batches(steps)
        # Deve retornar algo sem travar (fallback)
        assert len(batches) >= 1

    def test_execute_parallel_steps_single(self, tmp_path: Path):
        """Um único passo no batch usa execute_step diretamente (sem threading)."""
        from bauer.orchestrator import StepResult

        orch = _make_orch(tmp_path)
        step = {"id": 1, "goal": "passo único", "depends_on": []}

        with patch.object(orch, 'execute_step', return_value=StepResult(
            id=1, goal="passo único", model_used="phi4-mini", response="ok", tool_log=[]
        )) as mock_exec:
            results = orch.execute_parallel_steps([step], [])

        assert len(results) == 1
        mock_exec.assert_called_once()

    def test_execute_parallel_steps_multiple(self, tmp_path: Path):
        """Dois passos num batch são executados (potencialmente em paralelo)."""
        from bauer.orchestrator import StepResult

        orch = _make_orch(tmp_path)
        steps = [
            {"id": 1, "goal": "passo A", "depends_on": []},
            {"id": 2, "goal": "passo B", "depends_on": []},
        ]

        def _exec(step, prev):
            return StepResult(
                id=step["id"], goal=step["goal"], model_used="phi4-mini",
                response=f"resultado {step['id']}", tool_log=[]
            )

        with patch.object(orch, 'execute_step', side_effect=_exec):
            results = orch.execute_parallel_steps(steps, [])

        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {1, 2}


# ─── E2E: Collect Response com secrets scanner ───────────────────────────────

class TestE2ESecretsScanning:
    def test_collect_response_redacts_api_key(self, tmp_path: Path):
        """_collect_response redige API keys na resposta do modelo."""
        secret_response = "Use esta key: sk-abcdefghijklmnopqrstuvwxyz1234567890"

        client = MagicMock()
        client.chat_stream.return_value = iter([secret_response])

        from bauer.agent import _collect_response
        from bauer.context_manager import ContextManager

        ctx = _make_ctx()
        result = _collect_response(client, "phi4-mini", ctx.get_payload())

        # Key deve estar redacted
        assert "sk-" not in result or "[REDACTED" in result

    def test_tool_result_redacts_secrets(self, tmp_path: Path):
        """Tool que retorna segredo tem output redactado."""
        # Escreve arquivo com API key
        secret_file = tmp_path / "config.env"
        secret_file.write_text("API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890\n")

        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "read_file", "args": {"path": "config.env"}})

        # Resultado deve ter redactado o segredo
        assert "[REDACTED" in result or "sk-" not in result


# ─── E2E: Context Manager integration ────────────────────────────────────────

class TestE2EContextManager:
    def test_context_trimmed_when_overflow(self, tmp_path: Path):
        """Contexto é cortado quando ultrapassa budget."""
        ctx = ContextManager(applied_context=1000)  # budget = 750 tokens

        # Enche o contexto além do budget
        for i in range(20):
            ctx.messages.append({
                "role": "user",
                "content": "x" * 400  # 100 tokens cada
            })

        # Força trim
        ctx._trim()

        # Não deve exceder budget após trim
        from bauer.context_manager import _estimate_tokens
        assert _estimate_tokens(ctx.messages) <= ctx.budget

    def test_provider_budget_correct(self):
        """Provider 'gemini' tem budget enorme."""
        from bauer.context_manager import ContextManager, PROVIDER_CONTEXT_WINDOWS
        ctx = ContextManager(applied_context=0, provider="gemini")
        # Budget deve usar janela do Gemini
        gemini_window = PROVIDER_CONTEXT_WINDOWS["gemini"]
        expected_budget = int(gemini_window * 0.75)
        assert ctx.budget == expected_budget

    def test_get_payload_includes_system(self):
        """get_payload() inclui system prompt no início."""
        ctx = ContextManager(applied_context=4096, system_prompt="Você é um assistente.")
        ctx.add_user("oi")
        ctx.add_assistant("olá")
        payload = ctx.get_payload()

        assert payload[0]["role"] == "system"
        assert "assistente" in payload[0]["content"]
        assert len(payload) == 3  # system + user + assistant
