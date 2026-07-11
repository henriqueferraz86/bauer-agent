"""Roteamento por task_type/complexity (Fase 12, Sprint 34).

Classificador heurístico (sem LLM) + decide + comando `bauer models route`.
Conservador: sinal fraco cai em balanced, nunca no tier fraco por engano."""

from __future__ import annotations

import json

import pytest

from bauer.model_router import ModelProfile, classify_task, decide, profiles_from_config


class TestClassifyTask:
    @pytest.mark.parametrize("msg,task,tier", [
        ("oi, tudo bem?", "conversation", "fast"),
        ("obrigado!", "conversation", "fast"),
        ("liste os arquivos do projeto", "tool_call", "fast"),
        ("crie um script python que soma dois numeros", "coding", "coding"),
        ("refatore a arquitetura do runtime para suportar multiplos backends",
         "architecture", "heavy"),
    ])
    def test_task_and_tier(self, msg, task, tier):
        d = classify_task(msg)
        assert d.task_type == task
        assert d.profile == tier

    def test_code_block_is_coding(self):
        d = classify_task("o que esse trecho faz?\n```py\nprint(1)\n```")
        assert d.task_type == "coding"

    def test_unknown_falls_back_to_reasoning_balanced(self):
        # sem sinais fortes → caminho seguro (nunca 'fast' por engano)
        d = classify_task("me ajude a pensar sobre o problema que estou enfrentando aqui")
        assert d.task_type == "reasoning"
        assert d.profile == "balanced"

    def test_architecture_signal_forces_high(self):
        d = classify_task("redesenhe o sistema inteiro")
        assert d.complexity == "high"
        assert d.profile == "heavy"

    def test_reason_is_populated(self):
        d = classify_task("crie uma função")
        assert d.reason and d.task_type in d.reason


class TestDecideWithProfiles:
    def test_resolves_model_from_profile(self):
        profiles = {
            "heavy": ModelProfile("heavy", "openrouter", "anthropic/claude-sonnet-4"),
            "fast": ModelProfile("fast", "openrouter", "google/gemini-2.5-flash-lite"),
        }
        d = decide("refatore a arquitetura do sistema inteiro", profiles)
        assert d.profile == "heavy"
        assert d.provider == "openrouter"
        assert d.model == "anthropic/claude-sonnet-4"

    def test_no_profiles_leaves_model_empty(self):
        d = decide("oi", None)
        assert d.profile == "fast"
        assert d.model == ""

    def test_profiles_from_config(self):
        class _Models:
            profiles = {"fast": {"provider": "openrouter", "model": "gemini-flash-lite"}}

        class _Cfg:
            models = _Models()

        profs = profiles_from_config(_Cfg())
        assert profs["fast"].provider == "openrouter"
        assert profs["fast"].model == "gemini-flash-lite"

    def test_profiles_from_config_absent_is_empty(self):
        class _Cfg:
            pass
        assert profiles_from_config(_Cfg()) == {}


class TestRouteCli:
    def test_route_json(self):
        pytest.importorskip("typer")
        from typer.testing import CliRunner

        from bauer.cli import app

        res = CliRunner().invoke(app, ["models", "route", "liste os arquivos",
                                       "--format", "json", "--config", "/tmp/none-xyz"])
        assert res.exit_code == 0
        data = json.loads(res.stdout)
        assert data["task_type"] == "tool_call"
        assert data["profile"] == "fast"
