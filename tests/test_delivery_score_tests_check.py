"""O item `tests` do Delivery Score não pode contar teste de DEPENDÊNCIA.

Achado num projeto real (2026-07-28): projeto com `tests/` VAZIA marcava
`tests: ✓` porque o fallback `root.rglob("test_*.py")` varria o `.venv/`
junto — 1300 matches, todos de pytest/urllib3/numpy, zero do projeto.

O Delivery Score é gate de V1. Um item que passa sozinho quando alguém roda
`python -m venv .venv` não mede nada.
"""

from __future__ import annotations

from bauer.app_factory import _has_tests


def _venv_falso(root, n=3):
    """Simula dependências instaladas trazendo os próprios testes."""
    sp = root / ".venv" / "lib" / "python3.12" / "site-packages"
    for pkg in ("pytest", "urllib3", "numpy")[:n]:
        d = sp / pkg / "tests"
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_algo.py").write_text("def test_x(): pass\n", encoding="utf-8")


def test_venv_sozinho_nao_conta_como_ter_testes(tmp_path):
    """O caso que quebrou: tests/ vazia + venv instalado."""
    (tmp_path / "tests").mkdir()
    _venv_falso(tmp_path)
    assert _has_tests(tmp_path) is False


def test_teste_de_verdade_em_tests_conta(tmp_path):
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_health.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    _venv_falso(tmp_path)  # ruído junto: não pode atrapalhar
    assert _has_tests(tmp_path) is True


def test_teste_ao_lado_do_modulo_conta(tmp_path):
    """Fallback legítimo: quem não usa tests/ e põe o teste junto do código."""
    d = tmp_path / "app" / "core"
    d.mkdir(parents=True)
    (d / "test_config.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    _venv_falso(tmp_path)
    assert _has_tests(tmp_path) is True


def test_node_modules_nao_conta(tmp_path):
    d = tmp_path / "node_modules" / "alguma-lib" / "__tests__"
    d.mkdir(parents=True)
    (d / "index.test.js").write_text("test('x', () => {});\n", encoding="utf-8")
    assert _has_tests(tmp_path) is False


def test_teste_js_do_projeto_conta(tmp_path):
    d = tmp_path / "frontend" / "src"
    d.mkdir(parents=True)
    (d / "App.test.ts").write_text("test('x', () => {});\n", encoding="utf-8")
    assert _has_tests(tmp_path) is True


def test_projeto_vazio_nao_tem_testes(tmp_path):
    assert _has_tests(tmp_path) is False
