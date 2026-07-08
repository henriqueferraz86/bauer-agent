"""Testes para os gaps vs Hermes: cronjob, session_search, mixture_of_agents, video_analyze."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import warnings

from bauer.tool_router import ToolError, ToolRouter

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


@pytest.fixture
def router_with_client(ws: Path) -> ToolRouter:
    mock_client = MagicMock()
    mock_client.model = "gpt-4o"  # G18.4: modelo multimodal p/ passar no gate de visão
    return ToolRouter(workspace=ws, llm_client=mock_client)


# ===========================================================================
# CRONJOB
# ===========================================================================

class TestCronjob:

    # ── create ──────────────────────────────────────────────────────────────

    def test_create_every_minutes(self, router):
        r = router._cronjob({
            "action": "create",
            "name": "job1",
            "command": "print('ok')",
            "schedule": "every 5m",
        })
        assert "job1" in r
        assert "criado" in r

    def test_create_every_hours(self, router):
        r = router._cronjob({
            "action": "create",
            "name": "backup",
            "command": "print('backup')",
            "schedule": "every 2h",
            "mode": "python",
        })
        assert "backup" in r
        data = router._cronjob_load()
        assert data["backup"]["schedule"]["unit"] == "hours"
        assert data["backup"]["schedule"]["value"] == 2

    def test_create_every_days(self, router):
        router._cronjob({
            "action": "create",
            "name": "daily_job",
            "command": "print('daily')",
            "schedule": "every 1d",
        })
        data = router._cronjob_load()
        assert data["daily_job"]["schedule"]["unit"] == "days"

    def test_create_daily_time(self, router):
        router._cronjob({
            "action": "create",
            "name": "morning",
            "command": "print('bom dia')",
            "schedule": "daily 08:30",
        })
        data = router._cronjob_load()
        assert data["morning"]["schedule"]["type"] == "daily"
        assert data["morning"]["schedule"]["hour"] == 8
        assert data["morning"]["schedule"]["minute"] == 30

    def test_create_cron_expression(self, router):
        router._cronjob({
            "action": "create",
            "name": "cron_job",
            "command": "print('cron')",
            "schedule": "cron: */10 * * * *",
        })
        data = router._cronjob_load()
        assert data["cron_job"]["schedule"]["type"] == "cron"
        assert "*/10" in data["cron_job"]["schedule"]["expression"]

    def test_create_shell_mode(self, router):
        router._cronjob({
            "action": "create",
            "name": "shell_job",
            "command": "echo hello",
            "schedule": "every 1h",
            "mode": "shell",
        })
        data = router._cronjob_load()
        assert data["shell_job"]["mode"] == "shell"

    def test_create_duplicado_levanta(self, router):
        router._cronjob({
            "action": "create", "name": "dup",
            "command": "x=1", "schedule": "every 1h",
        })
        with pytest.raises(ToolError, match="ja existe"):
            router._cronjob({
                "action": "create", "name": "dup",
                "command": "x=2", "schedule": "every 2h",
            })

    def test_create_sem_name_levanta(self, router):
        with pytest.raises(ToolError, match="name"):
            router._cronjob({"action": "create", "command": "x=1", "schedule": "every 1h"})

    def test_create_sem_command_levanta(self, router):
        with pytest.raises(ToolError, match="command"):
            router._cronjob({"action": "create", "name": "j", "schedule": "every 1h"})

    def test_create_sem_schedule_levanta(self, router):
        with pytest.raises(ToolError, match="schedule"):
            router._cronjob({"action": "create", "name": "j", "command": "x=1"})

    def test_create_schedule_invalido_levanta(self, router):
        with pytest.raises(ToolError):
            router._cronjob({
                "action": "create", "name": "j",
                "command": "x=1", "schedule": "weekly monday",
            })

    def test_create_mode_invalido_levanta(self, router):
        with pytest.raises(ToolError, match="mode"):
            router._cronjob({
                "action": "create", "name": "j",
                "command": "x=1", "schedule": "every 1h",
                "mode": "ruby",
            })

    # ── list ────────────────────────────────────────────────────────────────

    def test_list_vazio(self, router):
        r = router._cronjob({"action": "list"})
        assert "Nenhum" in r

    def test_list_mostra_jobs(self, router):
        router._cronjob({
            "action": "create", "name": "j1",
            "command": "x=1", "schedule": "every 1h",
        })
        router._cronjob({
            "action": "create", "name": "j2",
            "command": "x=2", "schedule": "daily 09:00",
        })
        r = router._cronjob({"action": "list"})
        assert "j1" in r
        assert "j2" in r

    # ── delete ──────────────────────────────────────────────────────────────

    def test_delete_remove_job(self, router):
        router._cronjob({
            "action": "create", "name": "del_me",
            "command": "x=1", "schedule": "every 1h",
        })
        r = router._cronjob({"action": "delete", "name": "del_me"})
        assert "removido" in r
        data = router._cronjob_load()
        assert "del_me" not in data

    def test_delete_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="nao encontrado"):
            router._cronjob({"action": "delete", "name": "nao_existe"})

    def test_delete_sem_name_levanta(self, router):
        with pytest.raises(ToolError, match="name"):
            router._cronjob({"action": "delete"})

    # ── run ─────────────────────────────────────────────────────────────────

    def test_run_python_job(self, router):
        router._cronjob({
            "action": "create", "name": "run_me",
            "command": "print('executado')", "schedule": "every 1h",
        })
        r = router._cronjob({"action": "run", "name": "run_me"})
        assert "executado" in r
        data = router._cronjob_load()
        assert data["run_me"]["run_count"] == 1
        assert data["run_me"]["last_run"] is not None

    def test_run_shell_job(self, router):
        router._cronjob({
            "action": "create", "name": "shell_run",
            "command": "python -c \"print('shell ok')\"", "schedule": "every 1h",
            "mode": "shell",
        })
        r = router._cronjob({"action": "run", "name": "shell_run"})
        assert "shell ok" in r or "exit:" in r

    def test_run_incrementa_run_count(self, router):
        router._cronjob({
            "action": "create", "name": "counter",
            "command": "x=1", "schedule": "every 1h",
        })
        router._cronjob({"action": "run", "name": "counter"})
        router._cronjob({"action": "run", "name": "counter"})
        data = router._cronjob_load()
        assert data["counter"]["run_count"] == 2

    def test_run_inexistente_levanta(self, router):
        with pytest.raises(ToolError, match="nao encontrado"):
            router._cronjob({"action": "run", "name": "ghost"})

    # ── pause / resume ───────────────────────────────────────────────────────

    def test_pause_e_resume(self, router):
        router._cronjob({
            "action": "create", "name": "pausable",
            "command": "x=1", "schedule": "every 1h",
        })
        r_pause = router._cronjob({"action": "pause", "name": "pausable"})
        assert "paused" in r_pause
        data = router._cronjob_load()
        assert data["pausable"]["status"] == "paused"

        r_resume = router._cronjob({"action": "resume", "name": "pausable"})
        assert "active" in r_resume
        data = router._cronjob_load()
        assert data["pausable"]["status"] == "active"

    def test_acao_desconhecida_levanta(self, router):
        with pytest.raises(ToolError, match="desconhecida"):
            router._cronjob({"action": "trigger"})

    # ── parse_schedule ───────────────────────────────────────────────────────

    def test_parse_every_30m(self, router):
        s = router._parse_schedule("every 30m")
        assert s == {"type": "interval", "unit": "minutes", "value": 30}

    def test_parse_every_3h(self, router):
        s = router._parse_schedule("every 3h")
        assert s["unit"] == "hours"
        assert s["value"] == 3

    def test_parse_every_7d(self, router):
        s = router._parse_schedule("every 7d")
        assert s["unit"] == "days"

    def test_parse_daily(self, router):
        s = router._parse_schedule("daily 14:45")
        assert s["type"] == "daily"
        assert s["hour"] == 14
        assert s["minute"] == 45

    def test_parse_cron(self, router):
        s = router._parse_schedule("cron: 0 9 * * 1-5")
        assert s["type"] == "cron"
        assert "0 9 * * 1-5" in s["expression"]


# ===========================================================================
# SESSION_SEARCH
# ===========================================================================

class TestSessionSearch:

    def test_search_encontra_em_memory(self, router):
        router._memory({"action": "set", "key": "projeto", "value": "Bauer Agent Python"})
        r = router._session_search({"action": "search", "query": "Python", "source": "memory"})
        assert "Python" in r or "projeto" in r

    def test_search_sem_resultados(self, router):
        r = router._session_search({
            "action": "search",
            "query": "xyz_nao_existe_123",
            "source": "memory",
        })
        assert "Nenhum" in r or "nao encontrado" in r.lower()

    def test_search_regex(self, router):
        router._memory({"action": "set", "key": "phone", "value": "11 99999-1234"})
        r = router._session_search({
            "action": "search",
            "query": r"\d{2}\s\d{5}",
            "source": "memory",
        })
        assert "phone" in r or "11 99999" in r

    def test_search_sem_query_levanta(self, router):
        with pytest.raises(ToolError, match="query"):
            router._session_search({"action": "search", "source": "memory"})

    def test_acao_invalida_levanta(self, router):
        with pytest.raises(ToolError, match="search.*recent"):
            router._session_search({"action": "listar"})

    def test_recent_retorna_entradas(self, router):
        router._memory({"action": "set", "key": "a", "value": "v1"})
        router._memory({"action": "set", "key": "b", "value": "v2"})
        r = router._session_search({"action": "recent", "n": 5, "source": "memory"})
        assert "a" in r or "b" in r

    def test_recent_memory_vazia(self, router):
        r = router._session_search({"action": "recent", "source": "memory"})
        assert "recente" in r.lower() or "Nenhuma" in r

    def test_search_em_arquivo_jsonl(self, router, ws):
        # Cria arquivo de sessão com conteúdo pesquisável
        session_file = ws / "session.jsonl"
        session_file.write_text(
            '{"role": "user", "content": "Qual e a capital do Brasil?"}\n',
            encoding="utf-8",
        )
        r = router._session_search({
            "action": "search",
            "query": "capital do Brasil",
            "source": "sessions",
        })
        assert "capital" in r.lower() or "session" in r.lower()

    def test_search_all_combina_fontes(self, router, ws):
        router._memory({"action": "set", "key": "target", "value": "encontre isso"})
        session_file = ws / "sess.jsonl"
        session_file.write_text('{"content": "encontre isso tambem"}\n', encoding="utf-8")
        r = router._session_search({
            "action": "search",
            "query": "encontre",
            "source": "all",
        })
        assert "encontre" in r.lower()

    def test_busca_case_insensitive(self, router):
        router._memory({"action": "set", "key": "lang", "value": "Python"})
        r = router._session_search({
            "action": "search",
            "query": "python",
            "source": "memory",
        })
        assert "lang" in r or "Python" in r


# ===========================================================================
# MIXTURE_OF_AGENTS
# ===========================================================================

class TestMixtureOfAgents:

    def test_sem_query_levanta(self, router):
        with pytest.raises(ToolError, match="query"):
            router._mixture_of_agents({})

    def test_sem_client_levanta(self, router):
        with pytest.raises(ToolError, match="llm_client"):
            router._mixture_of_agents({"query": "Qual é a melhor estratégia?"})

    def test_chama_perspectivas_em_paralelo(self, router_with_client):
        call_count = {"n": 0}

        def fake_run(client, messages):
            call_count["n"] += 1
            return f"Resposta {call_count['n']}"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            result = router_with_client._mixture_of_agents({
                "query": "Como melhorar performance?",
                "perspectives": "analitico|critico",
                "synthesize": "false",
            })

        # 2 perspectivas → 2 chamadas
        assert call_count["n"] == 2
        assert "ANALITICO" in result or "CRITICO" in result

    def test_sintetiza_quando_synthesize_true(self, router_with_client):
        call_count = {"n": 0}

        def fake_run(client, messages):
            call_count["n"] += 1
            return "Insight relevante"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            result = router_with_client._mixture_of_agents({
                "query": "Analise este problema",
                "perspectives": "critico|pragmatico",
                "synthesize": "true",
            })

        # 2 perspectivas + 1 síntese = 3 chamadas
        assert call_count["n"] == 3
        assert "SÍNTESE" in result or "ntese" in result

    def test_perspectives_custom(self, router_with_client):
        captured_prompts = []

        def fake_run(client, messages):
            system = next(
                (m["content"] for m in messages if m.get("role") == "system"), ""
            )
            captured_prompts.append(system)
            return "resposta"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            router_with_client._mixture_of_agents({
                "query": "Teste",
                "perspectives": "junior|senior",
                "synthesize": "false",
            })

        # Verifica que as perspectivas customizadas foram usadas
        assert any("junior" in p.lower() or "senior" in p.lower() for p in captured_prompts)

    def test_4_perspectivas_padrao(self, router_with_client):
        call_count = {"n": 0}

        def fake_run(client, messages):
            call_count["n"] += 1
            return "ok"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            router_with_client._mixture_of_agents({
                "query": "Questão complexa",
                "synthesize": "false",
            })

        # 4 perspectivas padrão
        assert call_count["n"] == 4

    def test_output_contem_query(self, router_with_client):
        with patch("bauer.tool_router.ToolRouter._llm_single_turn", return_value="resposta"):
            result = router_with_client._mixture_of_agents({
                "query": "Pergunta única aqui",
                "perspectives": "analitico",
                "synthesize": "false",
            })
        assert "Pergunta única" in result

    def test_perspectiva_com_erro_nao_quebra_tudo(self, router_with_client):
        call_count = {"n": 0}

        def fake_run(client, messages):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Falha simulada")
            return "resposta ok"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            result = router_with_client._mixture_of_agents({
                "query": "Teste resiliente",
                "perspectives": "analitico|critico",
                "synthesize": "false",
            })
        # Deve ter uma resposta ok e um erro, mas não quebrar
        assert "resposta ok" in result or "erro" in result


# ===========================================================================
# VIDEO_ANALYZE
# ===========================================================================

class TestVideoAnalyze:

    def test_sem_video_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="video"):
            router_with_client._video_analyze({"query": "O que tem no vídeo?"})

    def test_sem_query_levanta(self, router_with_client):
        with pytest.raises(ToolError, match="query"):
            router_with_client._video_analyze({"video": "https://example.com/v.mp4"})

    def test_sem_client_levanta(self, router):
        with pytest.raises(ToolError, match="llm_client"):
            router._video_analyze({
                "video": "https://example.com/v.mp4",
                "query": "Descreva",
            })

    def test_url_passa_para_provider(self, router_with_client):
        with patch("bauer.tool_router.ToolRouter._llm_single_turn", return_value="Um vídeo de gatos."):
            result = router_with_client._video_analyze({
                "video": "https://example.com/cats.mp4",
                "query": "O que tem no vídeo?",
            })
        assert "video_analyze" in result
        assert "gatos" in result

    def test_url_mensagem_tem_image_url(self, router_with_client):
        captured = {}

        def fake_run(client, messages):
            captured["messages"] = messages
            return "ok"

        with patch("bauer.tool_router.ToolRouter._llm_single_turn", side_effect=fake_run):
            router_with_client._video_analyze({
                "video": "https://example.com/v.mp4",
                "query": "Teste",
            })

        content = captured["messages"][0]["content"]
        assert any(c.get("type") == "image_url" for c in content)

    def test_arquivo_nao_existente_levanta(self, router_with_client, ws):
        with pytest.raises(ToolError, match="nao encontrado"):
            router_with_client._video_analyze({
                "video": "inexistente.mp4",
                "query": "Descreva",
            })

    def test_formato_nao_suportado_levanta(self, router_with_client, ws):
        bad_file = ws / "video.xyz"
        bad_file.write_bytes(b"fake")
        with pytest.raises(ToolError, match="nao suportado"):
            router_with_client._video_analyze({
                "video": "video.xyz",
                "query": "Descreva",
            })

    def test_sem_cv2_e_sem_pil_levanta_instrucao(self, router_with_client, ws):
        mp4_file = ws / "test.mp4"
        mp4_file.write_bytes(b"\x00" * 100)

        import sys
        cv2_backup = sys.modules.pop("cv2", None)
        pil_backup = sys.modules.pop("PIL", None)
        try:
            with patch("bauer.tools.media._package_available", return_value=False):
                with pytest.raises(ToolError, match="opencv"):
                    router_with_client._video_analyze({
                        "video": "test.mp4",
                        "query": "Descreva",
                    })
        finally:
            if cv2_backup:
                sys.modules["cv2"] = cv2_backup
            if pil_backup:
                sys.modules["PIL"] = pil_backup

    def test_gif_com_pil(self, router_with_client, ws):
        """Testa análise de GIF via PIL mock."""
        from unittest.mock import MagicMock, PropertyMock
        gif_file = ws / "anim.gif"
        gif_file.write_bytes(b"GIF89a" + b"\x00" * 50)

        mock_image = MagicMock()
        mock_image.n_frames = 3
        mock_image.convert.return_value = mock_image
        mock_image.save = MagicMock()

        import sys
        import types

        fake_pil = types.ModuleType("PIL")
        fake_pil_image = types.ModuleType("PIL.Image")
        fake_pil_image.open = MagicMock(return_value=mock_image)
        fake_pil.Image = fake_pil_image

        with patch.dict(sys.modules, {"PIL": fake_pil, "PIL.Image": fake_pil_image}):
            with patch("bauer.tools.media._package_available") as mock_avail:
                mock_avail.side_effect = lambda name: name == "PIL"
                with patch("bauer.tool_router.ToolRouter._llm_single_turn", return_value="Frame analisado."):
                    result = router_with_client._video_analyze({
                        "video": "anim.gif",
                        "query": "O que acontece?",
                        "max_frames": 2,
                    })
        assert "GIF" in result or "anim" in result


# ===========================================================================
# Integração: 30 tools registradas
# ===========================================================================

class TestRegistro30Tools:

    def test_total_30_tools(self, ws):
        r = ToolRouter(workspace=ws, web_enabled=True)
        assert len(r.get_tool_schemas()) >= 30  # cresce a cada wave

    def test_novas_4_tools_registradas(self, router):
        tools = router.available_tools()
        for name in ["cronjob", "session_search", "mixture_of_agents", "video_analyze"]:
            assert name in tools, f"Tool '{name}' nao registrada"

    def test_schemas_novas_tools_validos(self, router):
        schemas = {s["function"]["name"]: s for s in router.get_tool_schemas()}
        for name in ["cronjob", "session_search", "mixture_of_agents", "video_analyze"]:
            assert name in schemas
            fn = schemas[name]["function"]
            assert fn["description"]
            assert "properties" in fn["parameters"]
