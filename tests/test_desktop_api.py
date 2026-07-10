"""Tests for desktop_api — endpoints REST/SSE do Bauer Desktop (DESK-A2)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from bauer import desktop_api as da  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

class TestMaskSecrets:
    def test_masks_key_fields(self):
        out = da._mask_secrets({"api_key": "sk-123456789", "name": "x"})
        assert out["api_key"].startswith("sk-1")
        assert out["api_key"] != "sk-123456789"
        assert out["name"] == "x"

    def test_short_secret_fully_masked(self):
        out = da._mask_secrets({"token": "ab"})
        assert out["token"] == "•••"

    def test_empty_secret_untouched(self):
        out = da._mask_secrets({"api_key": ""})
        assert out["api_key"] == ""

    def test_nested(self):
        out = da._mask_secrets({"telegram": {"token": "abcdefgh", "enabled": True}})
        assert out["telegram"]["token"] != "abcdefgh"
        assert out["telegram"]["enabled"] is True

    def test_list_of_dicts(self):
        out = da._mask_secrets({"items": [{"secret": "xyzzy123"}]})
        assert out["items"][0]["secret"] != "xyzzy123"


class TestCostHelpers:
    def _write(self, p: Path, recs):
        p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")

    def test_summary_today_vs_total(self, tmp_path):
        now = time.time()
        old = now - 3 * 86400
        f = tmp_path / "cost.jsonl"
        self._write(f, [
            {"ts": now, "cost_usd": 0.01, "total_tokens": 100, "session_id": "a"},
            {"ts": now, "cost_usd": 0.02, "total_tokens": 200, "session_id": "b"},
            {"ts": old, "cost_usd": 0.05, "total_tokens": 500, "session_id": "c"},
        ])
        s = da.cost_summary(f, now=now)
        assert s["cost_today_usd"] == 0.03
        assert s["tokens_today"] == 300
        assert s["calls_today"] == 2
        assert s["sessions_today"] == 2
        assert s["cost_total_usd"] == 0.08

    def test_summary_missing_file(self, tmp_path):
        s = da.cost_summary(tmp_path / "nope.jsonl")
        assert s["cost_today_usd"] == 0.0
        assert s["calls_today"] == 0

    def test_by_model_aggregates_sorted(self, tmp_path):
        f = tmp_path / "cost.jsonl"
        self._write(f, [
            {"model": "claude", "cost_usd": 0.1, "total_tokens": 10},
            {"model": "groq", "cost_usd": 0.3, "total_tokens": 30},
            {"model": "claude", "cost_usd": 0.05, "total_tokens": 5},
        ])
        rows = da.cost_by_model(f)
        assert rows[0]["model"] == "groq"
        assert rows[1]["model"] == "claude"
        assert rows[1]["cost_usd"] == 0.15
        assert rows[1]["calls"] == 2

    def test_percentile(self):
        assert da._percentile([], 50) is None
        assert da._percentile([10.0], 95) == 10.0
        assert da._percentile([1.0, 2.0, 3.0, 4.0], 50) is not None


class TestTailLog:
    def test_missing_returns_empty(self, tmp_path):
        assert da.tail_log(tmp_path / "nope.log") == []

    def test_returns_last_n(self, tmp_path):
        f = tmp_path / "x.log"
        f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        assert da.tail_log(f, lines=3) == ["line7", "line8", "line9"]


class TestReadJsonl:
    def test_skips_bad_lines(self, tmp_path):
        f = tmp_path / "x.jsonl"
        f.write_text('{"a":1}\nNOTJSON\n{"b":2}\n', encoding="utf-8")
        recs = da._read_jsonl(f)
        assert recs == [{"a": 1}, {"b": 2}]

    def test_limit_last_n(self, tmp_path):
        f = tmp_path / "x.jsonl"
        f.write_text("\n".join(json.dumps({"i": i}) for i in range(5)), encoding="utf-8")
        recs = da._read_jsonl(f, limit=2)
        assert recs == [{"i": 3}, {"i": 4}]


# ---------------------------------------------------------------------------
# Router via TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """App isolado: registry/profile globais e arquivos apontando p/ tmp."""
    reg = tmp_path / "projects.json"
    active_profile = tmp_path / "active_profile"
    monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
    monkeypatch.setattr("bauer.config_profiles._ACTIVE_PROFILE_FILE", active_profile)

    cost_file = tmp_path / "cost.jsonl"
    spans_file = tmp_path / "spans.jsonl"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  provider: opencode\n  name: deepseek\n"
        "telegram:\n  enabled: true\n  bot_token: secret-token-123\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = FastAPI()
    app.include_router(da.build_desktop_router(
        get_config_path=lambda: config_path,
        get_workspace=lambda: workspace,
        cost_file=cost_file,
        spans_file=spans_file,
        runtime_root=tmp_path / "runtime",
        logs_dir=logs_dir,
    ))
    client = TestClient(app)
    return {
        "client": client, "tmp": tmp_path, "cost_file": cost_file,
        "spans_file": spans_file, "logs_dir": logs_dir, "config_path": config_path,
        "runtime_root": tmp_path / "runtime",
    }


class TestProjectsEndpoints:
    def test_list_empty(self, env):
        r = env["client"].get("/api/projects")
        assert r.status_code == 200
        assert r.json() == {"projects": [], "active": None}

    def test_add_and_list(self, env):
        proj = env["tmp"] / "P1"
        proj.mkdir()
        r = env["client"].post("/api/projects", json={"path": str(proj)})
        assert r.status_code == 200
        lst = env["client"].get("/api/projects").json()
        assert len(lst["projects"]) == 1
        assert lst["active"] == lst["projects"][0]["id"]

    def test_add_missing_path_400(self, env):
        assert env["client"].post("/api/projects", json={}).status_code == 400

    def test_add_bad_dir_400(self, env):
        r = env["client"].post("/api/projects", json={"path": str(env["tmp"] / "nope")})
        assert r.status_code == 400

    def test_activate_unknown_404(self, env):
        assert env["client"].post("/api/projects/zzz/activate").status_code == 404

    def test_delete_unknown_404(self, env):
        assert env["client"].delete("/api/projects/zzz").status_code == 404

    def test_full_lifecycle(self, env):
        proj = env["tmp"] / "P2"
        proj.mkdir()
        pid = env["client"].post("/api/projects", json={"path": str(proj)}).json()["id"]
        assert env["client"].post(f"/api/projects/{pid}/activate").status_code == 200
        assert env["client"].get(f"/api/projects/{pid}/stats").status_code == 200
        assert env["client"].delete(f"/api/projects/{pid}").status_code == 200

    def test_workspace_folders_auto_discovered(self, env):
        """Regressão: as pastas de projeto do workspace (criadas pelo agente)
        nunca apareciam na tela Projetos — o registro era só manual."""
        ws = env["tmp"] / "workspace"
        (ws / "barbearia-site").mkdir()
        (ws / "bauerinvest").mkdir()
        (ws / "__pycache__").mkdir()      # lixo: não deve virar projeto
        (ws / ".git").mkdir()             # oculto: não deve virar projeto
        (ws / "node_modules").mkdir()     # lixo: não deve virar projeto
        (ws / "notas.txt").write_text("x", encoding="utf-8")  # arquivo: ignora

        r = env["client"].get("/api/projects")
        assert r.status_code == 200
        names = {p["name"] for p in r.json()["projects"]}
        assert {"barbearia-site", "bauerinvest"} <= names
        assert "__pycache__" not in names
        assert ".git" not in names
        assert "node_modules" not in names


class TestKanbanEndpoint:
    def test_empty_workspace(self, env):
        r = env["client"].get("/api/kanban")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_groups_by_status(self, env):
        class FakeTask:
            def __init__(self, id, status, title):
                self.id, self.status, self.title = id, status, title
                self.priority, self.assignee = "high", "bauer"

        tasks = [FakeTask("001", "TODO", "a"), FakeTask("002", "DONE", "b")]
        with patch("bauer.workspace_manager.WorkspaceManager") as WM:
            WM.return_value.list_tasks.return_value = tasks
            r = env["client"].get("/api/kanban")
        data = r.json()
        assert data["total"] == 2
        assert "TODO" in data["columns"] and "DONE" in data["columns"]


class TestKanbanProjectAware:
    """Fase 1: /api/kanban lê o board do PROJETO resolvido (não o workspace
    raiz fixo) via resolve_project_workspace — mesma resolução do chat."""

    def _app_with_resolver(self, tmp_path, resolver):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(da.build_desktop_router(
            get_workspace=lambda: tmp_path / "serve_ws",
            resolve_project_workspace=resolver,
            runtime_root=tmp_path / "runtime",
        ))
        return TestClient(app)

    def _seed_tasks(self, workspace: Path, titles: list[str]):
        from bauer.workspace_manager import WorkspaceManager

        workspace.mkdir(parents=True, exist_ok=True)
        wm = WorkspaceManager(workspace)
        for t in titles:
            wm.add_task(t)

    def test_reads_active_project_board(self, tmp_path):
        proj = tmp_path / "serve_ws" / "bauerinvest"
        self._seed_tasks(proj, ["tarefa do bauerinvest"])
        # resolver ignora project_id e sempre devolve o projeto ativo (bauerinvest)
        client = self._app_with_resolver(tmp_path, lambda pid: proj)

        data = client.get("/api/kanban").json()
        titles = [c["title"] for col in data["columns"].values() for c in col]
        assert "tarefa do bauerinvest" in titles

    def test_explicit_project_id_is_forwarded_to_resolver(self, tmp_path):
        seen = {}

        def resolver(pid):
            seen["pid"] = pid
            return tmp_path / "serve_ws"

        client = self._app_with_resolver(tmp_path, resolver)
        client.get("/api/kanban?project_id=abc123")
        assert seen["pid"] == "abc123"

    def test_falls_back_to_default_when_resolver_raises(self, tmp_path):
        default_ws = tmp_path / "serve_ws"
        self._seed_tasks(default_ws, ["tarefa da raiz"])

        def _boom(pid):
            raise RuntimeError("resolver quebrado")

        client = self._app_with_resolver(tmp_path, _boom)
        data = client.get("/api/kanban").json()
        titles = [c["title"] for col in data["columns"].values() for c in col]
        assert "tarefa da raiz" in titles  # caiu no get_workspace() default


class TestModelsCatalog:
    def _models(self):
        # catalog_models() sempre seta is_free — o filtro do endpoint confia
        # só nesse campo (custo 0 não implica grátis: ver _is_free_model).
        return [
            {"id": "claude-sonnet", "provider": "anthropic", "cost_in": 3.0, "is_free": False},
            {"id": "deepseek-free", "provider": "opencode", "cost_in": 0, "is_free": True},
            {"id": "llama-70b", "provider": "groq", "cost_in": 0.6, "is_free": False},
        ]

    def test_returns_all(self, env):
        with patch("bauer.models_dev.catalog_models", return_value=self._models()):
            r = env["client"].get("/api/models/catalog")
        assert r.json()["total"] == 3

    def test_filter_q(self, env):
        with patch("bauer.models_dev.catalog_models", return_value=self._models()):
            r = env["client"].get("/api/models/catalog?q=llama")
        data = r.json()
        assert data["total"] == 1
        assert data["models"][0]["id"] == "llama-70b"

    def test_filter_free(self, env):
        with patch("bauer.models_dev.catalog_models", return_value=self._models()):
            r = env["client"].get("/api/models/catalog?free=true")
        data = r.json()
        assert data["total"] == 1
        assert data["models"][0]["id"] == "deepseek-free"

    def test_pagination(self, env):
        with patch("bauer.models_dev.catalog_models", return_value=self._models()):
            r = env["client"].get("/api/models/catalog?limit=1&offset=1")
        data = r.json()
        assert data["total"] == 3
        assert len(data["models"]) == 1


class TestGatewayEndpoints:
    def test_status_reads_config(self, env):
        with patch("bauer.gateway_service.read_process_status", return_value=(None, None, None)):
            r = env["client"].get("/api/gateway/status")
        data = r.json()
        assert data["telegram"] is True
        assert data["running"] is False

    def test_status_running(self, env):
        with patch("bauer.gateway_service.read_process_status", return_value=(123, 60.0, 12.0)):
            r = env["client"].get("/api/gateway/status")
        data = r.json()
        assert data["pid"] == 123
        assert data["running"] is True

    def test_control_invalid_action_400(self, env):
        assert env["client"].post("/api/gateway/restart").status_code == 400

    def test_control_start(self, env):
        with patch("bauer.gateway_service.GatewayServiceManager") as M:
            M.return_value.start.return_value = "iniciado"
            r = env["client"].post("/api/gateway/start")
        assert r.json()["detail"] == "iniciado"


class TestObsEndpoints:
    def test_summary_with_spans(self, env):
        now = time.time()
        env["cost_file"].write_text(
            json.dumps({"ts": now, "cost_usd": 0.01, "total_tokens": 50, "session_id": "a"}),
            encoding="utf-8",
        )
        env["spans_file"].write_text(
            "\n".join(json.dumps({"duration_ms": d}) for d in (10, 20, 30)),
            encoding="utf-8",
        )
        r = env["client"].get("/api/obs/summary")
        data = r.json()
        assert data["calls_today"] == 1
        assert data["p50_ms"] is not None

    def test_cost_breakdown(self, env):
        env["cost_file"].write_text(
            json.dumps({"model": "claude", "cost_usd": 0.1, "total_tokens": 10}),
            encoding="utf-8",
        )
        r = env["client"].get("/api/obs/cost")
        data = r.json()
        assert data["by_model"][0]["model"] == "claude"

    def test_traces_filter_session(self, env):
        env["spans_file"].write_text(
            "\n".join([
                json.dumps({"trace_id": "t1", "name": "a"}),
                json.dumps({"trace_id": "t2", "name": "b"}),
            ]),
            encoding="utf-8",
        )
        r = env["client"].get("/api/obs/traces?session=t1")
        data = r.json()
        assert len(data["spans"]) == 1
        assert data["spans"][0]["name"] == "a"


class TestConfigEndpoints:
    def test_get_masks_secret(self, env):
        r = env["client"].get("/api/config")
        cfg = r.json()["config"]
        assert cfg["telegram"]["bot_token"] != "secret-token-123"
        assert cfg["model"]["provider"] == "opencode"

    def test_put_sets_value(self, env):
        r = env["client"].put("/api/config", json={"key": "model.name", "value": "gpt-x"})
        assert r.status_code == 200
        # relê do arquivo
        again = env["client"].get("/api/config").json()["config"]
        assert again["model"]["name"] == "gpt-x"

    def test_put_missing_key_400(self, env):
        assert env["client"].put("/api/config", json={"value": "x"}).status_code == 400

    def test_put_env_key_routes_to_dotenv(self, env, monkeypatch):
        """Chaves *_API_KEY vão pro .env (card 'Segredos' da tela Config)."""
        # set_config_value usa env_path relativo ao CWD — isola no tmp para
        # jamais tocar o .env real do repositório durante os testes.
        monkeypatch.chdir(env["tmp"])
        r = env["client"].put(
            "/api/config",
            json={"key": "OPENROUTER_API_KEY", "value": "sk-or-teste-nao-real"},
        )
        data = r.json()
        assert r.status_code == 200
        assert data["dest"] == "env"
        dotenv = (env["tmp"] / ".env").read_text(encoding="utf-8")
        assert "OPENROUTER_API_KEY=sk-or-teste-nao-real" in dotenv
        # E não vazou pro config.yaml (que é versionado).
        assert "sk-or-teste-nao-real" not in env["config_path"].read_text(encoding="utf-8")

    def test_profiles_list(self, env):
        # cria um profile config.dev.yaml ao lado do config.yaml
        (env["config_path"].parent / "config.dev.yaml").write_text("model:\n  name: x\n")
        r = env["client"].get("/api/config/profiles")
        data = r.json()
        assert "dev" in data["profiles"]

    def test_profile_use(self, env):
        r = env["client"].post("/api/config/profiles/prod/use")
        assert r.json()["active"] == "prod"


class TestLogsEndpoint:
    def test_tail(self, env):
        (env["logs_dir"] / "gateway.log").write_text("a\nb\nc\n", encoding="utf-8")
        r = env["client"].get("/api/logs/gateway/tail?lines=2")
        data = r.json()
        assert data["name"] == "gateway.log"
        assert data["lines"] == ["b", "c"]

    def test_tail_redacts_telegram_token(self, env):
        # token de bot Telegram embutido numa URL não pode vazar para a UI.
        # Token SINTÉTICO (formato \d{8,10}:[A-Za-z0-9_-]{35}), não um segredo real.
        token = "1234567890:" + "A" * 35
        (env["logs_dir"] / "gateway.log").write_text(
            f"POST https://api.telegram.org/bot{token}/getUpdates 200 OK\n",
            encoding="utf-8",
        )
        r = env["client"].get("/api/logs/gateway/tail")
        joined = "\n".join(r.json()["lines"])
        assert token not in joined
        assert "REDACTED" in joined

    def test_path_traversal_blocked(self, env):
        assert env["client"].get("/api/logs/..%2f..%2fetc%2fpasswd/tail").status_code in (400, 404)

    def test_missing_log_empty(self, env):
        r = env["client"].get("/api/logs/nonexistent/tail")
        assert r.json()["lines"] == []


class TestRuntimeDashboardEndpoints:
    def test_runtime_dashboard(self, env):
        r = env["client"].get("/api/runtime/dashboard")
        data = r.json()
        assert r.status_code == 200
        assert "bauer_native" in {adapter["name"] for adapter in data["adapters"]}
        assert "agno" in {adapter["name"] for adapter in data["adapters"]}
        assert "workers" in data
        assert data["kill_switch"] is False

    def test_agents_dashboard(self, env):
        r = env["client"].get("/api/agents")
        assert r.status_code == 200
        assert isinstance(r.json()["agents"], list)

    def test_skills_dashboard(self, env):
        r = env["client"].get("/api/skills")
        data = r.json()
        assert r.status_code == 200
        assert any(skill["id"] == "bauer.coding" for skill in data["skills"])

    def test_approval_actions(self, env):
        from bauer.core.policy import ApprovalManager

        record = ApprovalManager(root=env["runtime_root"]).request(
            operation="shell.execute",
            tool_name="run_command",
            reason="needs approval",
            risk_level="high",
        )
        approved = env["client"].post(f"/api/approvals/{record.id}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        missing = env["client"].post("/api/approvals/nope/deny")
        assert missing.status_code == 404


class TestBauerOsCommandEndpoint:
    def test_os_command_navigates_to_runs(self, env):
        r = env["client"].post("/api/os/command", json={"text": "mostrar runs"})
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "navigate"
        assert data["path"] == "/runs"

    def test_os_command_control_panel_requires_approval(self, env):
        r = env["client"].post("/api/os/command", json={"text": "abrir painel de controle"})
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "approval_required"
        assert data["approval"]["operation"] == "os.ui_control"
        approvals = env["client"].get("/api/obs/approvals").json()["approvals"]
        assert any(item["id"] == data["approval"]["id"] for item in approvals)

    def test_os_command_creates_agent_run(self, env):
        r = env["client"].post("/api/os/command", json={"text": "rodar agent code"})
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "run_created"
        assert data["run"]["agent_id"] == "code"
        runs = env["client"].get("/api/obs/runs").json()["runs"]
        assert any(item["id"] == data["run"]["id"] for item in runs)

    def test_os_command_pause_agent_is_audited(self, env):
        r = env["client"].post("/api/os/command", json={"text": "pausar agente code"})
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "agent_pause_requested"
        assert data["agent_id"] == "code"
        events = env["client"].get("/api/events").json()["events"]
        assert any(event["tool_name"] == "bauer_os.pause_agent" for event in events)

    def test_os_command_long_text_routes_intent_to_skill(self, env):
        """Fluxo de aceite Sprint 24: intenção → skill → policy → executa → evento."""
        from bauer.os_intent import IntentDecision

        decision = IntentDecision(
            skill_id="windows.browser",
            inputs={"url": "https://www.google.com/search?q=agno+docs"},
            confidence=0.9,
            reason="pesquisa na web",
        )
        with patch("bauer.os_intent.route_intent", return_value=decision), \
                patch("bauer.core.skills.windows.webbrowser.open", return_value=True) as opened:
            r = env["client"].post(
                "/api/os/command",
                json={"text": "abre o navegador e pesquisa docs do agno"},
            )
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "skill_executed"
        assert data["skill_id"] == "windows.browser"
        assert data["output"]["url"].startswith("https://www.google.com/search")
        opened.assert_called_once()
        # Execução auditada: eventos de skill publicados no Event Bus.
        events = env["client"].get("/api/events").json()["events"]
        types = {event["event_type"] for event in events}
        assert "skill.selected" in types
        assert "skill.executed" in types

    def test_os_command_intent_high_risk_requires_approval(self, env):
        """Skill com permissão shell.execute cai em approval, não executa direto."""
        from bauer.os_intent import IntentDecision

        decision = IntentDecision(
            skill_id="windows.powershell_safe",
            inputs={"command": "Get-Process"},
            confidence=0.9,
            reason="comando de sistema",
        )
        with patch("bauer.os_intent.route_intent", return_value=decision):
            r = env["client"].post(
                "/api/os/command",
                json={"text": "roda um powershell listando os processos abertos"},
            )
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "approval_required"
        assert data["approval"]["operation"] == "skill.execute"
        approvals = env["client"].get("/api/obs/approvals").json()["approvals"]
        assert any(item["id"] == data["approval"]["id"] for item in approvals)

    def test_os_command_unknown_when_router_unavailable(self, env):
        """Sem LLM configurado, o fallback determinístico continua respondendo."""
        with patch("bauer.os_intent.route_intent", return_value=None):
            r = env["client"].post(
                "/api/os/command",
                json={"text": "faz alguma coisa muito vaga que nada reconhece"},
            )
        data = r.json()
        assert r.status_code == 200
        assert data["kind"] == "unknown"
        assert data["suggestions"]

    def test_os_command_short_navigation_still_wins_over_intent(self, env):
        """Atalhos curtos não pagam o custo do LLM (nem podem ser sequestrados)."""
        with patch("bauer.os_intent.route_intent") as router:
            r = env["client"].post("/api/os/command", json={"text": "mostrar runs"})
        assert r.json()["kind"] == "navigate"
        router.assert_not_called()


class TestAuthWiring:
    def test_verify_key_applied(self, tmp_path):
        def _deny():
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="no key")

        app = FastAPI()
        app.include_router(da.build_desktop_router(verify_key=_deny))
        client = TestClient(app)
        assert client.get("/api/projects").status_code == 401
