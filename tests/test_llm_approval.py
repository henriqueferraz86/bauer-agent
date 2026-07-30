"""Tests for G4 — LLM Tool Approval."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.llm_approval import LLMApprovalResult, _parse_response, llm_evaluate_tool


# ─── LLMApprovalResult ───────────────────────────────────────────────────────

class TestLLMApprovalResult:
    def test_allow_factory(self):
        r = LLMApprovalResult.allow("looks safe")
        assert r.approved is True
        assert r.confidence == 1.0
        assert "safe" in r.reason

    def test_deny_factory(self):
        r = LLMApprovalResult.deny("too risky", suggestion="use read_file instead")
        assert r.approved is False
        assert "risky" in r.reason
        assert "read_file" in r.suggestion

    def test_default_confidence(self):
        r = LLMApprovalResult(approved=True, reason="ok")
        assert r.confidence == 0.9

    def test_suggestion_optional(self):
        r = LLMApprovalResult(approved=True, reason="fine")
        assert r.suggestion == ""


# ─── _parse_response ─────────────────────────────────────────────────────────

class TestParseResponse:
    def test_parses_approved_true(self):
        raw = '{"approved": true, "confidence": 0.9, "reason": "safe", "suggestion": ""}'
        r = _parse_response(raw)
        assert r.approved is True
        assert r.confidence == 0.9
        assert r.reason == "safe"

    def test_parses_approved_false(self):
        raw = '{"approved": false, "confidence": 0.8, "reason": "dangerous", "suggestion": "use safer tool"}'
        r = _parse_response(raw)
        assert r.approved is False
        assert r.suggestion == "use safer tool"

    def test_extracts_json_from_prose(self):
        raw = 'Some text before {"approved": true, "confidence": 0.7, "reason": "ok", "suggestion": ""} and after'
        r = _parse_response(raw)
        assert r.approved is True

    def test_fallback_on_invalid_json(self):
        r = _parse_response("not json at all")
        assert r.approved is True  # fail-open

    def test_fallback_on_malformed_json(self):
        r = _parse_response("{bad json}")
        assert r.approved is True  # fail-open

    def test_defaults_approved_true_when_missing(self):
        raw = '{"reason": "no approved field", "confidence": 0.5, "suggestion": ""}'
        r = _parse_response(raw)
        assert r.approved is True


# ─── llm_evaluate_tool ───────────────────────────────────────────────────────

#: O G4 so roda com juiz INDEPENDENTE (HARNESS-030) — sem isso ele se desliga em
#: vez de pedir ao modelo que audite a tool que ele mesmo escolheu. Os testes
#: abaixo exercitam o PARSING da resposta do juiz, entao declaram que existe um.
def _com_juiz():
    return patch("bauer.llm_approval.juiz_independente", return_value=True)


class TestLlmEvaluateTool:
    def test_safe_tool_auto_approved(self):
        result = llm_evaluate_tool("read_file", {"path": "foo.py"}, [])
        assert result.approved is True
        assert result.confidence == 1.0

    def test_no_aux_client_fails_open(self):
        with patch("bauer.llm_approval.call_aux_text", return_value=""):
            result = llm_evaluate_tool("delete_file", {"path": "x"}, [])
        assert result.approved is True  # fail-open when empty response

    def test_approved_when_client_returns_approved(self):
        good_json = '{"approved": true, "confidence": 0.95, "reason": "dev task", "suggestion": ""}'
        with _com_juiz(), patch("bauer.llm_approval.call_aux_text", return_value=good_json):
            result = llm_evaluate_tool("delete_file", {"path": "tmp.txt"},
                                       [{"role": "user", "content": "delete the temp file"}])
        assert result.approved is True
        assert result.confidence == 0.95

    def test_rejected_when_client_denies(self):
        bad_json = '{"approved": false, "confidence": 0.9, "reason": "suspicious", "suggestion": "check first"}'
        with _com_juiz(), patch("bauer.llm_approval.call_aux_text", return_value=bad_json):
            result = llm_evaluate_tool("run_command", {"command": "rm -rf /"},
                                       [{"role": "user", "content": "clean disk"}])
        assert result.approved is False
        assert "suspicious" in result.reason

    def test_exception_fails_open(self):
        with patch("bauer.llm_approval.call_aux_text", side_effect=RuntimeError("network error")):
            result = llm_evaluate_tool("delete_file", {"path": "x"}, [])
        assert result.approved is True  # fail-open on error

    def test_recent_messages_trimmed_to_6(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        good_json = '{"approved": true, "confidence": 0.8, "reason": "ok", "suggestion": ""}'
        with _com_juiz(), patch("bauer.llm_approval.call_aux_text", return_value=good_json) as mock_aux:
            llm_evaluate_tool("delete_file", {"path": "x"}, messages)
        # call_aux_text was called — the function ran
        mock_aux.assert_called_once()


# ─── ToolRouter integration ───────────────────────────────────────────────────

class TestToolRouterApprovalIntegration:
    def _make_router(self, tmp_path: Path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path, audit_enabled=False)

    def test_set_context_stores_messages(self, tmp_path):
        router = self._make_router(tmp_path)
        msgs = [{"role": "user", "content": "hello"}]
        router.set_context(msgs)
        assert router._recent_messages == msgs

    def test_set_context_keeps_last_6(self, tmp_path):
        router = self._make_router(tmp_path)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        router.set_context(msgs)
        assert len(router._recent_messages) == 6
        assert router._recent_messages[-1]["content"] == "msg9"

    def test_approval_denies_high_risk_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        # Approval só roda quando o router tem um llm_client (um "cérebro") —
        # um router sem LLM pula a checagem (fail-open). Passa um dummy.
        router = ToolRouter(workspace=tmp_path, audit_enabled=False, llm_client=object())
        deny_json = '{"approved": false, "confidence": 0.9, "reason": "risky", "suggestion": "backup first"}'
        with _com_juiz(), patch("bauer.llm_approval.call_aux_text", return_value=deny_json):
            result = router.execute('{"action": "delete_file", "args": {"path": "important.txt"}}')
        assert "LLM Approval Negado" in result
        assert "risky" in result

    def test_no_llm_client_skips_approval(self, tmp_path):
        """Router sem llm_client NÃO deve disparar approval (fail-open) — mesmo
        que call_aux_text fosse negar. Regressão do flake de CI: delete_file/
        run_command em fixtures sem LLM faziam chamada de approval resolvida do
        config.yaml do CWD e podiam ser negados."""
        from bauer.tool_router import ToolError, ToolRouter
        router = ToolRouter(workspace=tmp_path, audit_enabled=False)  # sem llm_client
        deny_json = '{"approved": false, "confidence": 0.9, "reason": "risky"}'
        with patch("bauer.llm_approval.call_aux_text", return_value=deny_json) as mock_aux:
            # delete_file sem confirm deve chegar na validação e levantar ToolError,
            # NÃO ser negado pelo approval (que nem deve ser chamado).
            with pytest.raises(ToolError, match="confirm"):
                router.execute('{"action": "delete_file", "args": {"path": "x.txt"}}')
        mock_aux.assert_not_called()

    def test_dry_run_skips_approval(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path, dry_run=True, audit_enabled=False)
        # Even without aux client, dry_run should not trigger approval
        with patch("bauer.llm_approval.call_aux_text") as mock_aux:
            router.execute('{"action": "delete_file", "args": {"path": "x.txt"}}')
        mock_aux.assert_not_called()

    def test_low_risk_tool_skips_approval(self, tmp_path):
        router = self._make_router(tmp_path)
        with patch("bauer.llm_approval.call_aux_text") as mock_aux:
            router.execute('{"action": "list_dir", "args": {"path": "."}}')
        mock_aux.assert_not_called()


class TestJuizIndependente:
    """HARNESS-030 — o G4 não pode pedir ao modelo que audite a si mesmo.

    `auxiliary.approval_model` vazio não significa "sem juiz": `_resolve_slot`
    cai no modelo PRINCIPAL, e o gate passa a perguntar ao mesmo modelo se a tool
    que ele acabou de escolher é segura. Observado em produção com modelo local:
    negou `docker compose logs` com "não há consentimento claro" logo depois de o
    usuário ter pedido exatamente isso. O erro anda nos dois sentidos — nega
    trabalho legítimo e carimba o que ele próprio propôs.
    """

    @staticmethod
    def _cfg(aux_provider="", aux_model=""):
        from bauer.config_loader import (AuxiliarySection, AuxiliarySlot,
                                         BauerConfig, ModelSection)
        return BauerConfig(
            model=ModelSection(provider="ollama", name="qwen3-coder:30b"),
            auxiliary=AuxiliarySection(
                approval_model=AuxiliarySlot(provider=aux_provider, model=aux_model)),
        )

    def test_sem_approval_model_nao_e_independente(self):
        from bauer.llm_approval import juiz_independente
        assert juiz_independente(self._cfg()) is False

    def test_mesmo_modelo_nao_e_independente(self):
        from bauer.llm_approval import juiz_independente
        assert juiz_independente(self._cfg("ollama", "qwen3-coder:30b")) is False

    def test_modelo_diferente_e_independente(self):
        from bauer.llm_approval import juiz_independente
        assert juiz_independente(self._cfg("ollama", "gpt-oss:20b")) is True

    def test_provider_diferente_basta(self):
        """Mesmo nome de modelo em dois provedores são instâncias distintas —
        sem estado nem contexto compartilhado."""
        from bauer.llm_approval import juiz_independente
        assert juiz_independente(self._cfg("openrouter", "qwen3-coder:30b")) is True

    def test_gate_se_desliga_em_vez_de_autojulgar(self):
        """Melhor NENHUM gate de LLM que um autojulgado: as camadas
        determinísticas (allowlist, limiar de risco, aprovação humana) seguem
        valendo, e elas não têm conflito de interesse."""
        from bauer.llm_approval import llm_evaluate_tool

        chamou = []
        with patch("bauer.llm_approval.call_aux_text",
                   side_effect=lambda *a, **k: chamou.append(1) or ""):
            r = llm_evaluate_tool("run_command", {"command": "docker ps"},
                                  [{"role": "user", "content": "veja os containers"}],
                                  cfg=self._cfg())

        assert r.approved is True
        assert "sem juiz independente" in r.reason
        assert not chamou, "não pode nem chamar o modelo para se autojulgar"
