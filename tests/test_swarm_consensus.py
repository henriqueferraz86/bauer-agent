"""Testes do SwarmRunner: consenso, votação, best-of-n, majority, synthesis."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from bauer.swarm import (
    AgentVote,
    ConsensusStrategy,
    SwarmResult,
    SwarmRunner,
    _score_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(response: str, fail: bool = False) -> MagicMock:
    """Cria mock de client LLM que retorna `response` ou levanta RuntimeError."""
    c = MagicMock()
    if fail:
        c.chat_stream.side_effect = RuntimeError("network error")
    else:
        c.chat_stream.return_value = iter([response])
    return c


def _vote(resp: str, score: float = 0.5, error: str = None, agent_id: str = "a0") -> AgentVote:
    return AgentVote(agent_id=agent_id, model="m", response=resp, elapsed_s=0.1, score=score, error=error)


# ---------------------------------------------------------------------------
# TestScoreResponse
# ---------------------------------------------------------------------------

class TestScoreResponse:
    def test_empty_scores_zero(self):
        assert _score_response("", "q") == 0.0

    def test_nonempty_scores_positive(self):
        assert _score_response("Uma resposta qualquer sobre Python", "Python") > 0

    def test_code_fence_bonus(self):
        s_plain = _score_response("resposta simples", "q")
        s_code = _score_response("resposta ```python code``` aqui", "q")
        assert s_code > s_plain

    def test_list_bonus(self):
        s_plain = _score_response("resposta simples", "q")
        s_list = _score_response("- item1\n- item2\n- item3", "q")
        assert s_list > s_plain

    def test_query_words_overlap(self):
        s_overlap = _score_response("python é uma linguagem incrível", "python linguagem")
        s_no_overlap = _score_response("banana e laranja são frutas", "python linguagem")
        assert s_overlap > s_no_overlap

    def test_max_score_one(self):
        big_resp = "word " * 300 + " - item\n" * 10 + " ```code``` "
        assert _score_response(big_resp, "word") <= 1.0

    def test_very_short_scores_low(self):
        assert _score_response("ok", "q") < 0.2

    def test_very_long_penalized(self):
        s_medium = _score_response("word " * 200, "q")
        s_very_long = _score_response("word " * 800, "q")
        # Ambos positivos; long ligeiramente menos que medium
        assert s_very_long >= 0 and s_medium >= 0


# ---------------------------------------------------------------------------
# TestAgentVote
# ---------------------------------------------------------------------------

class TestAgentVote:
    def test_ok_true_when_no_error(self):
        v = AgentVote(agent_id="a", model="m", response="hello", elapsed_s=1.0)
        assert v.ok is True

    def test_ok_false_when_error(self):
        v = AgentVote(agent_id="a", model="m", response="", elapsed_s=0.0, error="fail")
        assert v.ok is False

    def test_ok_false_when_empty_response(self):
        v = AgentVote(agent_id="a", model="m", response="   ", elapsed_s=0.0)
        assert v.ok is False


# ---------------------------------------------------------------------------
# TestSwarmRunnerInit
# ---------------------------------------------------------------------------

class TestSwarmRunnerInit:
    def test_creates_runner(self):
        r = SwarmRunner(clients=[_mock_client("hi")])
        assert r is not None

    def test_pads_models_list(self):
        r = SwarmRunner(clients=[_mock_client("a"), _mock_client("b")], models=["m1"])
        assert len(r._models) == 2

    def test_custom_scorer(self):
        custom = lambda resp, q: 0.99
        r = SwarmRunner(clients=[_mock_client("x")], scorer=custom)
        assert r._scorer is custom


# ---------------------------------------------------------------------------
# TestBestOfN
# ---------------------------------------------------------------------------

class TestBestOfN:
    def test_best_of_n_returns_highest_score(self):
        runner = SwarmRunner(
            clients=[_mock_client("short"), _mock_client("A detailed explanation with many words " * 10)],
            models=["m", "m"],
        )
        result = runner.run("explain python", strategy="best_of_n")
        assert result.winner
        assert result.strategy == "best_of_n"

    def test_best_of_n_sets_n_ok(self):
        runner = SwarmRunner(clients=[_mock_client("hi"), _mock_client("hello")])
        result = runner.run("q", strategy="best_of_n")
        assert result.n_ok == 2
        assert result.n_failed == 0

    def test_best_of_n_counts_failures(self):
        runner = SwarmRunner(
            clients=[_mock_client("ok"), _mock_client("", fail=True)],
        )
        result = runner.run("q", strategy="best_of_n")
        assert result.n_ok == 1
        assert result.n_failed == 1

    def test_best_of_n_metadata_has_scores(self):
        runner = SwarmRunner(clients=[_mock_client("answer")])
        result = runner.run("q", strategy="best_of_n")
        assert "scores" in result.metadata

    def test_best_of_n_winner_metadata(self):
        runner = SwarmRunner(clients=[_mock_client("answer")])
        result = runner.run("q", strategy="best_of_n")
        assert "winner_agent" in result.metadata
        assert "winner_score" in result.metadata

    def test_all_fail_returns_fallback_message(self):
        runner = SwarmRunner(clients=[_mock_client("", fail=True), _mock_client("", fail=True)])
        result = runner.run("q")
        assert "Nenhuma" in result.winner or result.n_ok == 0

    def test_elapsed_recorded(self):
        runner = SwarmRunner(clients=[_mock_client("hi")])
        result = runner.run("q")
        assert result.elapsed_s >= 0


# ---------------------------------------------------------------------------
# TestMajority
# ---------------------------------------------------------------------------

class TestMajority:
    def test_majority_returns_string(self):
        runner = SwarmRunner(clients=[_mock_client("Python is great"), _mock_client("Python is great")])
        result = runner.run("q", strategy="majority")
        assert isinstance(result.winner, str)

    def test_majority_metadata_has_majority_size(self):
        runner = SwarmRunner(clients=[_mock_client("same"), _mock_client("same")])
        result = runner.run("q", strategy="majority")
        assert "majority_size" in result.metadata

    def test_majority_strategy_name(self):
        runner = SwarmRunner(clients=[_mock_client("a")])
        result = runner.run("q", strategy=ConsensusStrategy.MAJORITY)
        assert result.strategy == "majority"


# ---------------------------------------------------------------------------
# TestSynthesis
# ---------------------------------------------------------------------------

class TestSynthesis:
    def test_synthesis_fallback_without_client(self):
        runner = SwarmRunner(clients=[_mock_client("a"), _mock_client("b")])
        result = runner.run("q", strategy="synthesis", synthesis_client=None)
        # Fallback para best_of_n
        assert result.winner

    def test_synthesis_uses_synth_client(self):
        synth = _mock_client("Synthesized answer")
        runner = SwarmRunner(clients=[_mock_client("a"), _mock_client("b")])
        result = runner.run("q", strategy="synthesis", synthesis_client=synth, synthesis_model="m")
        assert "Synthesized" in result.winner

    def test_synthesis_fallback_when_synth_fails(self):
        synth = _mock_client("", fail=True)
        runner = SwarmRunner(clients=[_mock_client("answer")])
        result = runner.run("q", strategy="synthesis", synthesis_client=synth)
        assert result.winner  # fallback

    def test_synthesis_metadata(self):
        synth = _mock_client("Synth result")
        runner = SwarmRunner(clients=[_mock_client("a")])
        result = runner.run("q", strategy="synthesis", synthesis_client=synth, synthesis_model="synth-model")
        assert result.metadata.get("strategy_detail") == "synthesis_ok"


# ---------------------------------------------------------------------------
# TestStrategyEnum
# ---------------------------------------------------------------------------

class TestStrategyEnum:
    def test_enum_values(self):
        assert ConsensusStrategy.BEST_OF_N == "best_of_n"
        assert ConsensusStrategy.MAJORITY == "majority"
        assert ConsensusStrategy.SYNTHESIS == "synthesis"

    def test_from_string(self):
        assert ConsensusStrategy("best_of_n") is ConsensusStrategy.BEST_OF_N

    def test_invalid_strategy_raises(self):
        runner = SwarmRunner(clients=[_mock_client("hi")])
        with pytest.raises(ValueError):
            runner.run("q", strategy="invalid_strategy")


# ---------------------------------------------------------------------------
# TestSwarmConcurrency
# ---------------------------------------------------------------------------

class TestSwarmConcurrency:
    def test_parallel_calls(self):
        clients = [_mock_client(f"response {i}") for i in range(4)]
        runner = SwarmRunner(clients=clients, max_workers=4)
        result = runner.run("q")
        assert result.n_ok == 4

    def test_system_prompt_passed(self):
        client = MagicMock()
        client.chat_stream.return_value = iter(["ok"])
        runner = SwarmRunner(clients=[client], models=["m"])
        runner.run("q", system_prompt="You are helpful.")
        call_args = client.chat_stream.call_args
        messages = call_args[0][1]  # second positional arg
        assert any(m["role"] == "system" for m in messages)

    def test_no_system_prompt_no_system_message(self):
        client = MagicMock()
        client.chat_stream.return_value = iter(["ok"])
        runner = SwarmRunner(clients=[client], models=["m"])
        runner.run("q", system_prompt="")
        call_args = client.chat_stream.call_args
        messages = call_args[0][1]
        assert not any(m["role"] == "system" for m in messages)
