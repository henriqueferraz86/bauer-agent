"""Testes da Fase 3 — autonomia: cost meter, skill learning, benchmark, CB E2E."""

from __future__ import annotations

import pytest


# ─── 3.3 Cost meter ────────────────────────────────────────────────────────────

class TestCostMeter:
    def test_sem_sink_e_noop(self):
        from bauer.cost_meter import report_llm_cost
        cost = report_llm_cost("openai", "gpt-4o", {"prompt_tokens": 100, "completion_tokens": 50})
        assert cost >= 0.0  # não explode sem sink

    def test_sink_recebe_custo(self):
        from bauer.cost_meter import cost_sink, report_llm_cost
        received: list = []
        token = cost_sink.set(lambda p, m, u, c: received.append((p, m, c)))
        try:
            report_llm_cost("openai", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 500})
        finally:
            cost_sink.reset(token)
        assert len(received) == 1
        provider, model, cost = received[0]
        assert provider == "openai" and model == "gpt-4o"
        assert cost > 0  # gpt-4o tem preço na tabela

    def test_usage_vazio_nao_chama_sink(self):
        from bauer.cost_meter import cost_sink, report_llm_cost
        received: list = []
        token = cost_sink.set(lambda *a: received.append(a))
        try:
            report_llm_cost("openai", "gpt-4o", None)
            report_llm_cost("openai", "gpt-4o", {})
        finally:
            cost_sink.reset(token)
        assert received == []

    def test_sink_que_explode_nao_propaga(self):
        from bauer.cost_meter import cost_sink, report_llm_cost

        def bad_sink(*a):
            raise RuntimeError("boom")

        token = cost_sink.set(bad_sink)
        try:
            report_llm_cost("openai", "gpt-4o", {"prompt_tokens": 10, "completion_tokens": 5})
        finally:
            cost_sink.reset(token)
        # chegou aqui sem propagar — ok

    def test_provider_from_client(self):
        from bauer.cost_meter import provider_from_client

        class FakeOllama:
            host = "http://localhost:11434"

        class FakeOpencode:
            host = "https://opencode.ai/zen"

        assert provider_from_client(FakeOllama()) == "ollama"
        assert provider_from_client(FakeOpencode()) == "opencode"

    def test_collect_response_reporta_ao_sink(self, monkeypatch):
        """Fio completo: _collect_response → report_llm_cost → sink."""
        from bauer.agent import _collect_response
        from bauer.cost_meter import cost_sink

        class FakeClient:
            host = "https://api.openai.com"
            last_usage = {"prompt_tokens": 100, "completion_tokens": 20}

            def chat_stream(self, model, messages):
                yield "resposta"

        received: list = []
        token = cost_sink.set(lambda p, m, u, c: received.append(c))
        try:
            _collect_response(FakeClient(), "gpt-4o", [{"role": "user", "content": "oi"}])
        finally:
            cost_sink.reset(token)
        assert len(received) == 1


# ─── 3.4 Skill learning ────────────────────────────────────────────────────────

class _FakeStore:
    """Store com pedidos repetidos em sessões diferentes."""

    def __init__(self, sessions: dict[str, list[dict]]):
        self._sessions = sessions

    def list_sessions(self):
        return list(self._sessions)

    def load(self, sid):
        return self._sessions[sid]


def _ask(text):
    return {"role": "user", "content": text}


class TestSkillLearning:
    def test_pedido_repetido_em_3_sessoes_vira_candidato(self):
        from bauer.skill_learning import find_skill_candidates
        store = _FakeStore({
            "s1": [_ask("gere o relatorio semanal de vendas em formato markdown")],
            "s2": [_ask("gere o relatorio semanal de vendas em formato markdown agora")],
            "s3": [_ask("por favor gere o relatorio semanal de vendas em markdown")],
            "s4": [_ask("qual a previsão do tempo hoje em são paulo?")],
        })
        candidates = find_skill_candidates(min_occurrences=3, store=store)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.occurrences == 3
        assert len(c.sessions) == 3
        assert "relatorio" in c.slug or "vendas" in c.slug

    def test_repeticao_na_mesma_sessao_nao_conta(self):
        from bauer.skill_learning import find_skill_candidates
        store = _FakeStore({
            "s1": [
                _ask("gere o relatorio semanal de vendas em formato markdown"),
                _ask("gere o relatorio semanal de vendas em formato markdown v2"),
                _ask("gere o relatorio semanal de vendas em formato markdown v3"),
            ],
        })
        assert find_skill_candidates(min_occurrences=3, store=store) == []

    def test_pedidos_curtos_ignorados(self):
        from bauer.skill_learning import find_skill_candidates
        store = _FakeStore({
            "s1": [_ask("oi"), _ask("continua")],
            "s2": [_ask("oi"), _ask("ok")],
            "s3": [_ask("oi")],
        })
        assert find_skill_candidates(min_occurrences=3, store=store) == []

    def test_draft_yaml_e_instalavel(self, tmp_path):
        """O YAML gerado precisa ser aceito pelo SkillManager real."""
        from bauer.skill_learning import SkillCandidate, draft_skill_yaml
        from bauer.skill_system import SkillManager

        c = SkillCandidate(
            slug="relatorio_vendas",
            occurrences=3,
            examples=["gere o relatorio semanal de vendas do arquivo dados.csv em markdown"],
            sessions=["s1", "s2", "s3"],
        )
        yaml_text = draft_skill_yaml(c)
        mgr = SkillManager(tmp_path)
        skill = mgr.install_from_yaml(yaml_text)
        assert skill.name == "relatorio_vendas"
        # path virou parâmetro {target}
        rendered = skill.render({"target": "outro.csv"})
        assert "outro.csv" in rendered

    def test_store_indisponivel_retorna_vazio(self):
        from bauer.skill_learning import find_skill_candidates

        class BrokenStore:
            def list_sessions(self):
                raise RuntimeError("db corrompido")

        assert find_skill_candidates(store=BrokenStore()) == []


# ─── 3.5 Benchmark ─────────────────────────────────────────────────────────────

class _ScriptedClient:
    """Cliente que responde com script fixo por task (bridge JSON)."""

    def __init__(self):
        self.turn = 0

    def chat_stream(self, model, messages):
        last = str(messages[-1].get("content", ""))
        # Responde direto às tasks determinísticas do benchmark
        if "BAUER_OK" in last:
            yield "BAUER_OK"
        elif "17 multiplicado" in last:
            yield "391"
        elif "VERMELHO" in last:
            yield "VERMELHO AZUL VERDE"
        elif "nota.txt" in last:
            yield '{"action": "write_file", "args": {"path": "nota.txt", "content": "ola mundo"}}'
        elif "[Resultado de write_file]" in last:
            yield "Arquivo criado com sucesso."
        else:
            yield "não sei"


class TestBenchmark:
    def test_runner_produz_report_completo(self):
        from bauer.benchmark import TASKS, run_benchmark
        report = run_benchmark(_ScriptedClient(), "fake-model", provider="custom",
                               tasks=[t for t in TASKS if t.id in ("echo", "calc", "write", "format_follow")])
        assert len(report.results) == 4
        assert report.passed >= 3  # echo, calc, format passam; write depende do fluxo
        assert 0.0 <= report.score <= 1.0

    def test_task_com_erro_nao_aborta_serie(self):
        from bauer.benchmark import TASKS, run_benchmark

        class ExplodingClient:
            def chat_stream(self, model, messages):
                raise RuntimeError("provider caiu")
                yield  # pragma: no cover

        report = run_benchmark(ExplodingClient(), "x", tasks=[t for t in TASKS if t.id == "echo"])
        assert len(report.results) == 1
        assert report.results[0].passed is False

    def test_save_e_history_roundtrip(self, tmp_path):
        from bauer.benchmark import BenchmarkReport, TaskResult, load_history, save_report
        report = BenchmarkReport(model="m", provider="p", started_at="2026-06-10T12:00:00")
        report.results.append(TaskResult("echo", True, 1.2, 0))
        path = save_report(report, bench_dir=tmp_path)
        assert path.exists()
        history = load_history(bench_dir=tmp_path)
        assert len(history) == 1
        assert history[0]["score"] == 1.0
        assert history[0]["model"] == "m"

    def test_ids_das_tasks_sao_estaveis(self):
        """Os ids NUNCA podem mudar — quebraria a série histórica."""
        from bauer.benchmark import TASKS
        expected = {
            "echo", "calc", "write", "read", "multi_step", "json_extract",
            "count_files", "patch", "graceful_missing", "format_follow",
        }
        assert {t.id for t in TASKS} == expected


# ─── 3.2 Circuit breaker — E2E do fallback chain ───────────────────────────────

class TestCircuitBreakerFallbackE2E:
    def test_primary_open_pula_direto_para_fallback(self):
        """Com o circuito do primário OPEN, a call nem tenta o primário."""
        from bauer.agent import _collect_with_fallback
        from bauer.circuit_breaker import global_cb
        from rich.console import Console
        import io

        class PrimaryClient:
            host = "https://api.openai.com"  # resolve → "openai" via provider_from_client
            calls = 0

            def chat_stream(self, model, messages):
                PrimaryClient.calls += 1
                yield "primário"

        class FallbackClient:
            host = "http://localhost:11434"  # resolve → "ollama" via provider_from_client

            def chat_stream(self, model, messages):
                yield "resposta do fallback"

        # Força OPEN no provider do primário (openai); fallback (ollama) permanece fechado
        provider_key = "openai"
        try:
            for _ in range(20):
                global_cb.record_failure(provider_key, RuntimeError("down"))
            console = Console(file=io.StringIO())
            fallbacks = [(FallbackClient(), "fb-model", "fallback-label")]
            response, active, model = _collect_with_fallback(
                PrimaryClient(), "p-model",
                [{"role": "user", "content": "oi"}],
                fallbacks, console,
            )
            assert "fallback" in response
            assert PrimaryClient.calls == 0, "primário OPEN não deveria ser chamado"
        finally:
            # Limpa o estado do circuito para não vazar p/ outros testes
            try:
                global_cb.reset(provider_key)
            except Exception:
                pass
