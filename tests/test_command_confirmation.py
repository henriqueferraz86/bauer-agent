"""Testes da confirmação interativa de comando (allowlist que aprende).

Cobre os 2 gates:
- Gate 1 (padrões perigosos): callback do chat mapeia e→once/a→always/n→deny.
- Gate 2 (allowlist do shell): callback libera e "always" persiste/aprende.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from rich.console import Console


def _con():
    return Console(file=io.StringIO(), force_terminal=False)


# ─── Gate 1 — prompt de confirmação (padrão perigoso) ────────────────────────

class TestApprovalCallback:
    def _cb(self):
        from bauer.agent import _make_cli_approval_callback
        return _make_cli_approval_callback(_con())

    @pytest.mark.parametrize("resp,expected", [
        ("e", "once"), ("o", "once"), ("s", "session"),
        ("a", "always"), ("n", "deny"), ("d", "deny"), ("", "deny"),
        ("xyz", "deny"),
    ])
    def test_mapeia_resposta(self, resp, expected):
        cb = self._cb()
        with patch("builtins.input", return_value=resp):
            assert cb("rm -rf /tmp/x", "risco") == expected

    def test_eof_nega(self):
        cb = self._cb()
        with patch("builtins.input", side_effect=EOFError):
            assert cb("cmd", "d") == "deny"


# ─── Gate 2 — allowlist do shell (aprende com "always") ──────────────────────

class TestAllowlistLearning:
    def _runner(self, tmp_path, cb=None):
        from bauer.shell_runner import ShellRunner
        return ShellRunner(workspace=tmp_path, allowlist_callback=cb)

    def test_sem_callback_bloqueia(self, tmp_path):
        from bauer.shell_runner import BlockedCommandError
        r = self._runner(tmp_path)
        with pytest.raises(BlockedCommandError):
            r.validate("docker ps")

    def test_always_libera_e_persiste(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
        from bauer.shell_runner import load_learned_commands
        r = self._runner(tmp_path, cb=lambda base: "always")
        args = r.validate("docker ps")
        assert args[0] == "docker"
        assert "docker" in load_learned_commands()

    def test_nova_instancia_carrega_aprendido(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
        from bauer.shell_runner import add_learned_command, BlockedCommandError
        add_learned_command("kubectl")
        r = self._runner(tmp_path)  # sem callback
        r.validate("kubectl get pods")  # não levanta — aprendido
        # um comando não-aprendido ainda bloqueia
        with pytest.raises(BlockedCommandError):
            r.validate("terraform apply")

    def test_session_libera_sem_persistir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
        from bauer.shell_runner import load_learned_commands
        r = self._runner(tmp_path, cb=lambda base: "session")
        r.validate("docker ps")  # libera nesta instância
        r.validate("docker images")  # já no runtime, não re-pergunta
        assert "docker" not in load_learned_commands()  # NÃO persistiu

    def test_deny_bloqueia(self, tmp_path):
        from bauer.shell_runner import BlockedCommandError
        r = self._runner(tmp_path, cb=lambda base: "deny")
        with pytest.raises(BlockedCommandError):
            r.validate("docker ps")

    def test_callback_erro_bloqueia(self, tmp_path):
        from bauer.shell_runner import BlockedCommandError
        def _boom(base):
            raise RuntimeError("x")
        r = self._runner(tmp_path, cb=_boom)
        with pytest.raises(BlockedCommandError):
            r.validate("docker ps")

    def test_comando_ja_na_allowlist_nao_pergunta(self, tmp_path):
        calls = []
        r = self._runner(tmp_path, cb=lambda base: calls.append(base) or "deny")
        r.validate("git status")  # git está na allowlist fixa
        assert calls == []  # callback nem foi chamado


# ─── persistência (load/add) ─────────────────────────────────────────────────

class TestLearnedStore:
    def test_add_e_load(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
        from bauer.shell_runner import add_learned_command, load_learned_commands
        assert load_learned_commands() == set()
        add_learned_command("docker")
        add_learned_command("DOCKER")  # normaliza p/ minúsculo, não duplica
        add_learned_command("")        # ignora vazio
        assert load_learned_commands() == {"docker"}

    def test_load_arquivo_corrompido_vazio(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        monkeypatch.setenv("BAUER_HOME", str(home))
        (home / "allowed_commands.yaml").write_text("{{ lixo", encoding="utf-8")
        from bauer.shell_runner import load_learned_commands
        assert load_learned_commands() == set()


# ─── config toggle ───────────────────────────────────────────────────────────

def test_confirm_commands_default_true():
    from bauer.config_loader import ToolsSection
    assert ToolsSection().confirm_commands is True
