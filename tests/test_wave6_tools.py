"""Testes Wave 6 — Skills, Process, image_generate, text_to_speech, Kanban, Browser."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from bauer.kanban_store import KanbanStore
from bauer.task_dispatcher import TaskDispatcher
from bauer.tool_router import ToolError, ToolRouter
from bauer.workspace_manager import WorkspaceManager


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "https://api.openai.com/v1"
    client.api_key = "test-key"
    return client


@pytest.fixture
def router_with_client(ws: Path, mock_client) -> ToolRouter:
    return ToolRouter(workspace=ws, llm_client=mock_client)


# =============================================================================
# Skills system
# =============================================================================

class TestSkillManage:

    def test_create_skill_basico(self, router, ws):
        result = router._skill_manage({
            "action": "create",
            "name": "debug-python",
            "description": "Como debugar Python",
            "content": "1. Use pdb\n2. Use print()\n3. Use logging",
        })
        assert "criada" in result
        assert "debug-python" in result
        skills_file = ws / ".bauer_skills.json"
        assert skills_file.exists()
        data = json.loads(skills_file.read_text(encoding="utf-8"))
        assert "debug-python" in data
        assert data["debug-python"]["content"].startswith("1. Use pdb")

    def test_create_skill_com_tags(self, router):
        result = router._skill_manage({
            "action": "create",
            "name": "git-flow",
            "description": "Git workflow",
            "content": "feat: -> develop -> main",
            "tags": ["git", "workflow"],
        })
        assert "git" in result or "criada" in result

    def test_create_skill_duplicada_levanta(self, router):
        router._skill_manage({
            "action": "create", "name": "minha-skill",
            "description": "d", "content": "c",
        })
        with pytest.raises(ToolError, match="já existe"):
            router._skill_manage({
                "action": "create", "name": "minha-skill",
                "description": "d", "content": "c",
            })

    def test_update_skill(self, router):
        router._skill_manage({
            "action": "create", "name": "sk1",
            "description": "old desc", "content": "old content",
        })
        result = router._skill_manage({
            "action": "update", "name": "sk1",
            "description": "new desc", "content": "new content",
        })
        assert "atualizada" in result

    def test_delete_skill(self, router, ws):
        router._skill_manage({
            "action": "create", "name": "temp",
            "description": "d", "content": "c",
        })
        result = router._skill_manage({"action": "delete", "name": "temp"})
        assert "removida" in result
        data = json.loads((ws / ".bauer_skills.json").read_text(encoding="utf-8"))
        assert "temp" not in data

    def test_delete_inexistente_levanta(self, router):
        with pytest.raises(ToolError):
            router._skill_manage({"action": "delete", "name": "nao-existe"})

    def test_action_invalida_levanta(self, router):
        with pytest.raises(ToolError, match="inválida"):
            router._skill_manage({
                "action": "show", "name": "x",
                "description": "d", "content": "c",
            })

    def test_sem_action_levanta(self, router):
        with pytest.raises(ToolError, match="action"):
            router._skill_manage({"name": "x", "description": "d", "content": "c"})

    def test_sem_name_levanta(self, router):
        with pytest.raises(ToolError, match="name"):
            router._skill_manage({"action": "create", "description": "d", "content": "c"})

    def test_create_sem_description_levanta(self, router):
        with pytest.raises(ToolError, match="description"):
            router._skill_manage({"action": "create", "name": "x", "content": "c"})

    def test_create_sem_content_levanta(self, router):
        with pytest.raises(ToolError, match="content"):
            router._skill_manage({"action": "create", "name": "x", "description": "d"})


class TestSkillView:

    def test_view_existente(self, router):
        router._skill_manage({
            "action": "create", "name": "my-skill",
            "description": "Uma skill útil",
            "content": "Passo 1\nPasso 2",
            "tags": ["util"],
        })
        result = router._skill_view({"name": "my-skill"})
        assert "my-skill" in result
        assert "Passo 1" in result
        assert "Uma skill útil" in result

    def test_view_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="não encontrada"):
            router._skill_view({"name": "nao-existe"})

    def test_view_sem_name_levanta(self, router):
        with pytest.raises(ToolError, match="name"):
            router._skill_view({})


class TestSkillsList:

    def test_lista_vazia(self, router):
        result = router._skills_list({})
        assert "Nenhuma" in result

    def test_lista_com_skills(self, router):
        for i in range(3):
            router._skill_manage({
                "action": "create", "name": f"skill-{i}",
                "description": f"desc {i}", "content": f"content {i}",
                "tags": [f"tag{i}"],
            })
        result = router._skills_list({})
        assert "skill-0" in result
        assert "skill-1" in result
        assert "skill-2" in result

    def test_lista_com_filtro_nome(self, router):
        router._skill_manage({
            "action": "create", "name": "python-debug",
            "description": "debug", "content": "x", "tags": [],
        })
        router._skill_manage({
            "action": "create", "name": "git-flow",
            "description": "git", "content": "y", "tags": [],
        })
        result = router._skills_list({"filter": "python"})
        assert "python-debug" in result
        assert "git-flow" not in result

    def test_lista_com_filtro_tag(self, router):
        router._skill_manage({
            "action": "create", "name": "sk",
            "description": "d", "content": "c", "tags": ["automation"],
        })
        result = router._skills_list({"filter": "automation"})
        assert "sk" in result

    def test_filtro_sem_resultados(self, router):
        router._skill_manage({
            "action": "create", "name": "sk", "description": "d", "content": "c",
        })
        result = router._skills_list({"filter": "xyzabc123"})
        assert "Nenhuma" in result


# =============================================================================
# Process manager
# =============================================================================

class TestProcess:

    def test_start_e_list(self, ws):
        router = ToolRouter(workspace=ws)
        result = router._process({"action": "start", "command": "python -c \"import time; time.sleep(5)\"", "label": "sleeping"})
        assert "Iniciado" in result
        assert "sleeping" in result
        pid = result.split("PID ")[-1].strip()

        list_result = router._process({"action": "list"})
        assert "PID" in list_result
        # cleanup
        router._process({"action": "kill", "pid": pid})

    def test_list_vazia(self, ws):
        router = ToolRouter(workspace=ws)
        result = router._process({"action": "list"})
        assert "Nenhum" in result

    def test_start_sem_command_levanta(self, ws):
        router = ToolRouter(workspace=ws)
        with pytest.raises(ToolError, match="command"):
            router._process({"action": "start"})

    def test_sem_action_levanta(self, router):
        with pytest.raises(ToolError, match="action"):
            router._process({})

    def test_action_invalida_levanta(self, router):
        with pytest.raises(ToolError, match="inválida"):
            router._process({"action": "pause"})

    def test_poll_pid_invalido(self, router):
        with pytest.raises(ToolError, match="não encontrado"):
            router._process({"action": "poll", "pid": "99999"})

    def test_log_pid_invalido(self, router):
        with pytest.raises(ToolError, match="não encontrado"):
            router._process({"action": "log", "pid": "99999"})

    def test_kill_pid_invalido(self, router):
        with pytest.raises(ToolError, match="não encontrado"):
            router._process({"action": "kill", "pid": "99999"})

    def test_write_sem_input_levanta(self, ws):
        router = ToolRouter(workspace=ws)
        result = router._process({"action": "start", "command": "python -c \"import sys; x=sys.stdin.read()\"", "label": "reader"})
        pid = result.split("PID ")[-1].strip()
        with pytest.raises(ToolError, match="input"):
            router._process({"action": "write", "pid": pid})
        router._process({"action": "kill", "pid": pid})

    def test_poll_processo_finalizado(self, ws):
        router = ToolRouter(workspace=ws)
        result = router._process({"action": "start", "command": "python -c \"print('done')\"", "label": "quick"})
        pid = result.split("PID ")[-1].strip()
        import time
        time.sleep(0.5)
        poll = router._process({"action": "poll", "pid": pid})
        # pode estar running ou finalizado
        assert "PID" in poll

    def test_sem_pid_para_poll_levanta(self, router):
        with pytest.raises(ToolError, match="pid"):
            router._process({"action": "poll"})


# =============================================================================
# image_generate
# =============================================================================

class TestImageGenerate:

    def test_sem_prompt_levanta(self, router):
        with pytest.raises(ToolError, match="prompt"):
            router._image_generate({})

    def test_sem_llm_client_levanta(self, router):
        with pytest.raises(ToolError, match="llm_client"):
            router._image_generate({"prompt": "um gato"})

    def test_model_invalido_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="model"):
            router_with_client._image_generate({"prompt": "x", "model": "dall-e-99"})

    def test_size_invalido_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="size"):
            router_with_client._image_generate({"prompt": "x", "size": "800x600"})

    def _make_image_client(self, url="https://example.com/img.png"):
        """Cria mock de cliente que expõe .images.generate diretamente."""
        mock_img_response = MagicMock()
        mock_data = MagicMock()
        mock_data.url = url
        mock_img_response.data = [mock_data]
        client = MagicMock()
        client.images.generate.return_value = mock_img_response
        return client

    def test_gera_imagem_via_client_direto(self, router_with_client):
        client = self._make_image_client("https://example.com/img.png")
        router_with_client._llm_client = client
        result = router_with_client._image_generate({
            "prompt": "um cachorro pulando",
            "model": "dall-e-3",
        })
        assert "https://example.com/img.png" in result

    def test_gera_imagem_sem_output_file(self, router_with_client):
        client = self._make_image_client("https://img.example.com/abc.png")
        router_with_client._llm_client = client

        result = router_with_client._image_generate({"prompt": "paisagem"})
        assert "image_generate" in result
        assert "https://" in result

    def test_openai_import_error_levanta(self, router_with_client):
        # spec=[] → sem atributos → cai no else que faz `import openai`
        router_with_client._llm_client = MagicMock(spec=[])
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ToolError, match="openai"):
                router_with_client._image_generate({"prompt": "teste"})

    def test_api_error_levanta(self, router_with_client):
        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("API error 429")
        router_with_client._llm_client = client
        with pytest.raises(ToolError, match="API error"):
            router_with_client._image_generate({"prompt": "falha"})


# =============================================================================
# text_to_speech
# =============================================================================

class TestTextToSpeech:

    def test_sem_text_levanta(self, router):
        with pytest.raises(ToolError, match="text"):
            router._text_to_speech({"output_file": "out.mp3"})

    def test_texto_longo_levanta(self, router):
        with pytest.raises(ToolError, match="4096"):
            router._text_to_speech({"text": "x" * 4097, "output_file": "out.mp3"})

    def test_sem_output_file_levanta(self, router):
        with pytest.raises(ToolError, match="output_file"):
            router._text_to_speech({"text": "ola"})

    def test_sem_llm_client_levanta(self, router):
        with pytest.raises(ToolError, match="llm_client"):
            router._text_to_speech({"text": "ola", "output_file": "out.mp3"})

    def test_voice_invalida_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="voice"):
            router_with_client._text_to_speech({
                "text": "ola", "output_file": "out.mp3", "voice": "batman"
            })

    def test_model_invalido_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="model"):
            router_with_client._text_to_speech({
                "text": "ola", "output_file": "out.mp3", "model": "tts-3"
            })

    def test_gera_audio_via_client(self, router_with_client, mock_client, ws):
        # Cria cliente mock que expõe .audio.speech.create diretamente
        mock_client_direct = MagicMock()
        mock_client_direct.base_url = "https://api.openai.com/v1"
        mock_client_direct.api_key = "test-key"

        # stream_to_file precisa criar o arquivo
        def _write_file(path):
            Path(path).write_bytes(b"\xff\xfb" + b"\x00" * 100)

        mock_speech_resp = MagicMock()
        mock_speech_resp.stream_to_file.side_effect = _write_file
        mock_client_direct.audio.speech.create.return_value = mock_speech_resp
        router_with_client._llm_client = mock_client_direct

        result = router_with_client._text_to_speech({
            "text": "Olá mundo",
            "output_file": "audio.mp3",
            "voice": "nova",
        })
        assert "audio.mp3" in result
        assert "nova" in result

    def test_openai_import_error_levanta(self, router_with_client):
        # spec=[] → sem atributos → cai no else que faz `import openai`
        router_with_client._llm_client = MagicMock(spec=[])
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ToolError, match="openai"):
                router_with_client._text_to_speech({"text": "t", "output_file": "o.mp3"})


# =============================================================================
# Kanban
# =============================================================================

class TestKanbanCreate:

    def test_cria_tarefa_basica(self, router, ws):
        result = router._kanban_create({"title": "Implementar feature X"})
        assert "T0001" in result
        assert "Implementar feature X" in result
        board = router._load_kanban()
        assert "T0001" in board["tasks"]
        tasks = WorkspaceManager(ws).list_tasks()
        assert tasks[0].title == "Implementar feature X"

    def test_lista_tarefa_criada_pelo_workspace_manager(self, router, ws):
        WorkspaceManager(ws).add_task("Criada pelo humano")
        result = router._kanban_list({})
        assert "T0001" in result
        assert "Criada pelo humano" in result

    def test_cria_multiplas_tarefas(self, router):
        for i in range(5):
            result = router._kanban_create({"title": f"Tarefa {i}"})
            assert f"T000{i+1}" in result

    def test_sem_title_levanta(self, router):
        with pytest.raises(ToolError, match="title"):
            router._kanban_create({})

    def test_priority_invalida_levanta(self, router):
        with pytest.raises(ToolError, match="priority"):
            router._kanban_create({"title": "x", "priority": "ultra"})

    def test_cria_filho_registra_no_pai(self, router, ws):
        router._kanban_create({"title": "Pai"})
        router._kanban_create({"title": "Filho", "parent_id": "T0001"})
        board = router._load_kanban()
        assert "T0002" in board["tasks"]["T0001"]["children"]

    def test_campos_opcionais(self, router, ws):
        router._kanban_create({
            "title": "Task",
            "description": "Detalhes",
            "assignee": "agent-1",
            "priority": "high",
        })
        board = router._load_kanban()
        t = board["tasks"]["T0001"]
        assert t["assignee"] == "agent-1"
        assert t["priority"] == "high"
        assert t["description"] == "Detalhes"


class TestKanbanList:

    def test_lista_vazia(self, router):
        result = router._kanban_list({})
        assert "Nenhuma" in result

    def test_lista_todas(self, router):
        router._kanban_create({"title": "A"})
        router._kanban_create({"title": "B"})
        result = router._kanban_list({})
        assert "T0001" in result
        assert "T0002" in result

    def test_filtra_por_status(self, router):
        router._kanban_create({"title": "Todo"})
        router._kanban_create({"title": "Done"})
        router._kanban_complete({"task_id": "T0002"})
        result = router._kanban_list({"status": "done"})
        assert "T0002" in result
        assert "T0001" not in result

    def test_filtra_por_assignee(self, router):
        router._kanban_create({"title": "A", "assignee": "alice"})
        router._kanban_create({"title": "B", "assignee": "bob"})
        result = router._kanban_list({"assignee": "alice"})
        assert "alice" in result
        assert "T0001" in result
        assert "T0002" not in result

    def test_filtra_por_priority(self, router):
        router._kanban_create({"title": "High", "priority": "high"})
        router._kanban_create({"title": "Low", "priority": "low"})
        result = router._kanban_list({"priority": "high"})
        assert "T0001" in result
        assert "T0002" not in result


class TestKanbanShow:

    def test_show_existente(self, router):
        router._kanban_create({"title": "Minha Tarefa", "description": "Detalhes importantes"})
        result = router._kanban_show({"task_id": "T0001"})
        assert "Minha Tarefa" in result
        assert "Detalhes importantes" in result
        assert "todo" in result

    def test_show_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="não encontrada"):
            router._kanban_show({"task_id": "T9999"})

    def test_sem_task_id_levanta(self, router):
        with pytest.raises(ToolError, match="task_id"):
            router._kanban_show({})


class TestKanbanWorkflow:

    def test_complete_muda_status(self, router, ws):
        router._kanban_create({"title": "Tarefa"})
        result = router._kanban_complete({"task_id": "T0001", "result": "Sucesso!"})
        assert "done" in result or "T0001" in result
        board = router._load_kanban()
        assert board["tasks"]["T0001"]["status"] == "done"

    def test_block_e_unblock(self, router, ws):
        router._kanban_create({"title": "Task"})
        router._kanban_block({"task_id": "T0001", "reason": "Aguardando aprovação"})
        board = router._load_kanban()
        assert board["tasks"]["T0001"]["status"] == "blocked"

        router._kanban_unblock({"task_id": "T0001", "note": "Aprovado!"})
        board2 = router._load_kanban()
        assert board2["tasks"]["T0001"]["status"] == "todo"

    def test_heartbeat_muda_para_in_progress(self, router, ws):
        router._kanban_create({"title": "Task longa"})
        result = router._kanban_heartbeat({"task_id": "T0001", "progress": "50% concluído"})
        assert "50%" in result
        board = router._load_kanban()
        assert board["tasks"]["T0001"]["status"] == "in_progress"

    def test_comment_adiciona_sem_mudar_status(self, router, ws):
        router._kanban_create({"title": "Task"})
        router._kanban_comment({
            "task_id": "T0001", "comment": "Nota importante", "author": "supervisor"
        })
        board = router._load_kanban()
        comments = board["tasks"]["T0001"]["comments"]
        assert any("Nota importante" in c["text"] for c in comments)
        assert board["tasks"]["T0001"]["status"] == "todo"

    def test_link_cria_relacao(self, router, ws):
        router._kanban_create({"title": "Pai"})
        router._kanban_create({"title": "Filho"})
        result = router._kanban_link({"parent_id": "T0001", "child_id": "T0002"})
        assert "T0002" in result
        board = router._load_kanban()
        assert "T0002" in board["tasks"]["T0001"]["children"]
        assert board["tasks"]["T0002"]["parent_id"] == "T0001"

    def test_link_mesmo_id_levanta(self, router):
        router._kanban_create({"title": "x"})
        with pytest.raises(ToolError, match="iguais"):
            router._kanban_link({"parent_id": "T0001", "child_id": "T0001"})

    def test_block_sem_reason_levanta(self, router):
        router._kanban_create({"title": "x"})
        with pytest.raises(ToolError, match="reason"):
            router._kanban_block({"task_id": "T0001"})

    def test_heartbeat_sem_progress_levanta(self, router):
        router._kanban_create({"title": "x"})
        with pytest.raises(ToolError, match="progress"):
            router._kanban_heartbeat({"task_id": "T0001"})

    def test_comment_sem_comment_levanta(self, router):
        router._kanban_create({"title": "x"})
        with pytest.raises(ToolError, match="comment"):
            router._kanban_comment({"task_id": "T0001"})

    def test_worker_protocol_complete_updates_run(self, router, ws, monkeypatch):
        wm = WorkspaceManager(ws)
        task = wm.add_task("Worker owned")
        dispatcher = TaskDispatcher(ws)
        dispatcher.mark_ready(task.id)
        with dispatcher._lock():
            claimed = dispatcher._claim_locked(wm.get_task(task.id))

        monkeypatch.setenv("BAUER_KANBAN_TASK", claimed.id)
        monkeypatch.setenv("BAUER_KANBAN_CLAIM_ID", claimed.metadata["claim_id"])
        monkeypatch.setenv("BAUER_KANBAN_RUN_ID", claimed.metadata["run_id"])

        router._kanban_heartbeat({"task_id": "T0001", "progress": "working"})
        result = router._kanban_complete({"task_id": "T0001", "result": "finished by tool"})

        final = wm.get_task(task.id)
        run = KanbanStore(ws).get_run(claimed.metadata["run_id"])
        events = KanbanStore(ws).list_events(task_id=task.id, limit=20)
        assert "marcado como done" in result
        assert final.status == "DONE"
        assert "claim_id" not in final.metadata
        assert run is not None
        assert run.status == "succeeded"
        assert "worker.completed_by_tool" in {event.event_type for event in events}

    def test_worker_protocol_blocks_foreign_task(self, router, ws, monkeypatch):
        wm = WorkspaceManager(ws)
        first = wm.add_task("Pinned")
        second = wm.add_task("Foreign")
        dispatcher = TaskDispatcher(ws)
        dispatcher.mark_ready(first.id)
        with dispatcher._lock():
            claimed = dispatcher._claim_locked(wm.get_task(first.id))

        monkeypatch.setenv("BAUER_KANBAN_TASK", claimed.id)
        monkeypatch.setenv("BAUER_KANBAN_CLAIM_ID", claimed.metadata["claim_id"])
        monkeypatch.setenv("BAUER_KANBAN_RUN_ID", claimed.metadata["run_id"])

        with pytest.raises(ToolError, match="protocol violation"):
            router._kanban_complete({"task_id": second.id, "result": "nope"})

        events = KanbanStore(ws).list_events(task_id=first.id, limit=10)
        assert "worker.protocol_violation" in {event.event_type for event in events}

    def test_worker_context_filters_available_tools(self, ws):
        router = ToolRouter(workspace=ws, tool_context="worker")
        tools = router.available_tools()
        schemas = router.get_tool_schemas()
        schema_names = {schema["function"]["name"] for schema in schemas}

        assert "kanban_heartbeat" in tools
        assert "kanban_complete" in tools
        assert "write_file" in tools
        assert "kanban_create" not in tools
        assert "delegate_task" not in tools
        assert "browser_cdp" not in tools
        assert "kanban_create" not in schema_names

    def test_denied_worker_tool_records_event(self, ws, monkeypatch):
        wm = WorkspaceManager(ws)
        task = wm.add_task("Worker scoped")
        dispatcher = TaskDispatcher(ws)
        dispatcher.mark_ready(task.id)
        with dispatcher._lock():
            claimed = dispatcher._claim_locked(wm.get_task(task.id))

        monkeypatch.setenv("BAUER_KANBAN_TASK", claimed.id)
        monkeypatch.setenv("BAUER_KANBAN_CLAIM_ID", claimed.metadata["claim_id"])
        monkeypatch.setenv("BAUER_KANBAN_RUN_ID", claimed.metadata["run_id"])

        router = ToolRouter(workspace=ws, tool_context="worker")
        with pytest.raises(ToolError, match="tool denied"):
            router.execute({"action": "kanban_create", "args": {"title": "not allowed"}})

        store = KanbanStore(ws)
        events = store.list_events(task_id=task.id, limit=20)
        run = store.get_run(claimed.metadata["run_id"])
        assert "tool.denied" in {event.event_type for event in events}
        assert run is not None
        assert run.metadata["last_denied_tool"] == "kanban_create"

    def test_workspace_tool_policy_can_override_contexts(self, ws):
        policy_dir = ws / ".bauer"
        policy_dir.mkdir()
        (policy_dir / "tool_policy.yaml").write_text(
            """
contexts:
  supervisor:
    mode: allow_all
    deny: [write_file]
  worker:
    mode: allowlist
    allow: [kanban_heartbeat]
""".strip(),
            encoding="utf-8",
        )

        supervisor = ToolRouter(workspace=ws, tool_context="supervisor")
        worker = ToolRouter(workspace=ws, tool_context="worker")

        assert "write_file" not in supervisor.available_tools()
        assert supervisor.tool_info("write_file")["policy_source"].endswith("tool_policy.yaml")
        worker_tools = worker.available_tools()
        assert "kanban_heartbeat" in worker_tools
        assert "read_file" not in worker_tools
        with pytest.raises(ToolError, match="tool denied"):
            supervisor.execute({"action": "write_file", "args": {"path": "x.txt", "content": "x"}})


# =============================================================================
# Browser automation
# =============================================================================

class TestBrowserNavigate:

    def _mock_page(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        page.title.return_value = "Página de Teste"
        page.url = "https://example.com"
        return page

    def test_url_invalida_levanta(self, router):
        with pytest.raises(ToolError, match="http"):
            router._browser_navigate({"url": "ftp://example.com"})

    def test_sem_url_levanta(self, router):
        with pytest.raises(ToolError, match="http"):
            router._browser_navigate({"url": ""})

    def test_playwright_nao_instalado_levanta(self, router):
        with patch("builtins.__import__", side_effect=lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "playwright.sync_api" else __import__(n, *a, **k)):
            router._browser_page = None
            with pytest.raises(ToolError, match="Playwright"):
                router._browser_navigate({"url": "https://example.com"})

    def test_navega_com_page_mockada(self, router):
        mock_page = self._mock_page()
        router._browser_page = mock_page
        result = router._browser_navigate({"url": "https://example.com"})
        assert "example.com" in result
        mock_page.goto.assert_called_once()


class TestBrowserSnapshot:

    def test_snapshot_com_page_mockada(self, router):
        mock_page = MagicMock()
        mock_page.url = "https://example.com/page"
        mock_page.title.return_value = "Título"
        mock_page.evaluate.return_value = "body\n  h1 \"Título\"\n  p \"Parágrafo\""
        router._browser_page = mock_page

        result = router._browser_snapshot({})
        assert "Título" in result
        assert "Parágrafo" in result or "body" in result

    def test_snapshot_sem_browser_levanta(self, router):
        with patch("builtins.__import__", side_effect=lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "playwright.sync_api" else __import__(n, *a, **k)):
            router._browser_page = None
            with pytest.raises(ToolError, match="Playwright"):
                router._browser_snapshot({})


class TestBrowserInteraction:

    def _setup_router_with_page(self, router):
        mock_page = MagicMock()
        mock_page.url = "https://example.com"
        mock_page.title.return_value = "Test"
        router._browser_page = mock_page
        return mock_page

    def test_click_sem_selector_levanta(self, router):
        router._browser_page = MagicMock()
        with pytest.raises(ToolError, match="selector"):
            router._browser_click({"selector": ""})

    def test_click_by_css(self, router):
        page = self._setup_router_with_page(router)
        loc = MagicMock()
        page.locator.return_value.first = loc
        result = router._browser_click({"selector": "button.submit", "by": "css"})
        assert "Clicou" in result

    def test_click_by_text(self, router):
        page = self._setup_router_with_page(router)
        page.get_by_text.return_value.first = MagicMock()
        result = router._browser_click({"selector": "Enviar", "by": "text"})
        assert "Clicou" in result

    def test_type_sem_selector_levanta(self, router):
        router._browser_page = MagicMock()
        with pytest.raises(ToolError, match="selector"):
            router._browser_type({"selector": "", "text": "ola"})

    def test_type_fill(self, router):
        page = self._setup_router_with_page(router)
        loc = MagicMock()
        page.locator.return_value.first = loc
        result = router._browser_type({"selector": "input#name", "text": "João"})
        assert "Digitou" in result
        assert "4 chars" in result or "4" in result

    def test_scroll_down(self, router):
        page = self._setup_router_with_page(router)
        result = router._browser_scroll({"direction": "down", "amount": 300})
        assert "down" in result
        page.evaluate.assert_called()

    def test_scroll_top(self, router):
        page = self._setup_router_with_page(router)
        result = router._browser_scroll({"direction": "top"})
        assert "top" in result

    def test_back(self, router):
        page = self._setup_router_with_page(router)
        page.url = "https://example.com/prev"
        result = router._browser_back({})
        assert "Voltou" in result

    def test_press_sem_key_levanta(self, router):
        router._browser_page = MagicMock()
        with pytest.raises(ToolError, match="key"):
            router._browser_press({"key": ""})

    def test_press_key(self, router):
        page = self._setup_router_with_page(router)
        result = router._browser_press({"key": "Enter"})
        assert "Enter" in result
        page.keyboard.press.assert_called_with("Enter")

    def test_press_key_em_selector(self, router):
        page = self._setup_router_with_page(router)
        loc = MagicMock()
        page.locator.return_value.first = loc
        result = router._browser_press({"key": "Tab", "selector": "input"})
        assert "Tab" in result


class TestBrowserUtilities:

    def _setup(self, router):
        page = MagicMock()
        page.url = "https://example.com"
        router._browser_page = page
        return page

    def test_console_sem_mensagens(self, router):
        self._setup(router)
        router._BROWSER_CONSOLE_MSGS = []
        result = router._browser_console({})
        assert "Sem mensagens" in result

    def test_console_com_mensagens(self, router):
        self._setup(router)
        router._BROWSER_CONSOLE_MSGS = ["[log] hello", "[error] oops"]
        result = router._browser_console({"max_lines": 10})
        assert "hello" in result
        assert "oops" in result

    def test_get_images_sem_imagens(self, router):
        page = self._setup(router)
        page.evaluate.return_value = []
        result = router._browser_get_images({})
        assert "Nenhuma" in result

    def test_get_images_lista(self, router):
        page = self._setup(router)
        page.evaluate.return_value = [
            {"src": "https://ex.com/a.jpg", "alt": "foto", "width": 100, "height": 100}
        ]
        result = router._browser_get_images({})
        assert "a.jpg" in result
        assert "foto" in result

    def test_vision_sem_query_levanta(self, router):
        self._setup(router)
        with pytest.raises(ToolError, match="query"):
            router._browser_vision({"query": ""})

    def test_vision_sem_client_levanta(self, router):
        self._setup(router)
        with pytest.raises(ToolError, match="llm_client"):
            router._browser_vision({"query": "O que há na página?"})

    def test_vision_com_client(self, ws, mock_client):
        router = ToolRouter(workspace=ws, llm_client=mock_client)
        page = MagicMock()
        page.url = "https://example.com"
        page.screenshot.return_value = b"\x89PNG" + b"\x00" * 50
        router._browser_page = page

        with patch("bauer.agent.run_one_turn", return_value="Vi um botão vermelho."):
            result = router._browser_vision({"query": "O que tem na página?"})
        assert isinstance(result, str)

    def test_cdp_sem_method_levanta(self, router):
        self._setup(router)
        with pytest.raises(ToolError, match="method"):
            router._browser_cdp({"method": ""})

    def test_cdp_envia_comando(self, router):
        page = self._setup(router)
        mock_cdp = MagicMock()
        mock_cdp.send.return_value = {"result": "ok"}
        page.context.new_cdp_session.return_value = mock_cdp
        result = router._browser_cdp({"method": "Page.getFrameTree"})
        assert "Page.getFrameTree" in result
        assert "ok" in result

    def test_dialog_nenhum_pendente(self, router):
        page = self._setup(router)
        # Simula wait_for_timeout sem disparar dialog
        page.wait_for_timeout = MagicMock()
        page.remove_listener = MagicMock()
        result = router._browser_dialog({"action": "accept"})
        assert "Nenhum" in result or "dialog" in result.lower()


# =============================================================================
# Integração: 61 tools registradas (Wave 6 + channel + send_message/transcribe)
# =============================================================================

class TestRegistro57Tools:

    def test_total_61_tools(self, ws):
        router = ToolRouter(workspace=ws, web_enabled=True)
        # Lower-bound: o conjunto cresceu (G7 code-intel, G15 LSP, etc.).
        # >= em vez de == para nao quebrar a cada nova tool registrada.
        assert len(router._tools) >= 61

    def test_skills_registradas(self, ws):
        router = ToolRouter(workspace=ws)
        for name in ("skill_manage", "skill_view", "skills_list"):
            assert name in router._tools

    def test_process_registrado(self, ws):
        router = ToolRouter(workspace=ws)
        assert "process" in router._tools

    def test_media_tools_registradas(self, ws):
        router = ToolRouter(workspace=ws)
        assert "image_generate" in router._tools
        assert "text_to_speech" in router._tools

    def test_kanban_tools_registradas(self, ws):
        router = ToolRouter(workspace=ws)
        kanban_tools = [
            "kanban_create", "kanban_list", "kanban_show", "kanban_complete",
            "kanban_block", "kanban_unblock", "kanban_heartbeat", "kanban_comment", "kanban_link",
        ]
        for name in kanban_tools:
            assert name in router._tools, f"{name} não registrada"

    def test_browser_tools_registradas(self, ws):
        router = ToolRouter(workspace=ws)
        browser_tools = [
            "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
            "browser_scroll", "browser_back", "browser_press", "browser_console",
            "browser_get_images", "browser_vision", "browser_dialog", "browser_cdp",
        ]
        for name in browser_tools:
            assert name in router._tools, f"{name} não registrada"

    def test_tool_info_skill_manage(self, ws):
        router = ToolRouter(workspace=ws)
        info = router.tool_info("skill_manage")
        assert info["name"] == "skill_manage"
        assert "description" in info
        assert "args" in info

    def test_tool_info_kanban_create(self, ws):
        router = ToolRouter(workspace=ws)
        info = router.tool_info("kanban_create")
        assert "title" in info["args"]
