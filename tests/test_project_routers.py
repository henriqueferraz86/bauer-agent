"""Testes para ProjectRouterCache — cache LRU de ToolRouter por projeto (Fase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.project_routers import ProjectRouterCache
from bauer import projects_registry as pr


@pytest.fixture
def default_router() -> object:
    return object()  # sentinel — só precisa ser identidade distinta


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch, tmp_path):
    """Todo teste deste arquivo usa um projects.json isolado em tmp_path —
    nunca o ~/.bauer/projects.json real da máquina."""
    monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "projects.json")


class _FakeRouter:
    def __init__(self, workspace: Path):
        self.workspace = workspace


def _fake_builder(built: list):
    def _build(path: Path):
        built.append(path)
        return _FakeRouter(path)
    return _build


class TestProjectRouterCache:
    def test_none_project_id_returns_default(self, default_router):
        cache = ProjectRouterCache(default_router, _fake_builder([]))
        assert cache.get(None) is default_router
        assert cache.get("") is default_router

    def test_unknown_project_id_returns_default(self, default_router):
        cache = ProjectRouterCache(default_router, _fake_builder([]))
        assert cache.get("nao-existe") is default_router

    def test_valid_project_builds_and_caches(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        proj = tmp_path / "meu-projeto"
        proj.mkdir()
        pid = pr.add_project(proj)["id"]

        built: list = []
        default = object()
        cache = ProjectRouterCache(default, _fake_builder(built))

        router1 = cache.get(pid)
        assert router1 is not default
        assert router1.workspace == proj
        assert built == [proj]

        # Segunda chamada: vem do cache, NÃO reconstrói.
        router2 = cache.get(pid)
        assert router2 is router1
        assert built == [proj]  # builder não foi chamado de novo

    def test_deleted_project_folder_falls_back_to_default(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        proj = tmp_path / "vai-sumir"
        proj.mkdir()
        pid = pr.add_project(proj)["id"]
        import shutil
        shutil.rmtree(proj)

        default = object()
        cache = ProjectRouterCache(default, _fake_builder([]))
        assert cache.get(pid) is default

    def test_sensitive_dir_falls_back_to_default(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        # Registra a própria home como "projeto" (usuário mal-intencionado ou
        # config velha) — is_sensitive_dir deve barrar mesmo já registrado.
        home = Path.home()
        pid = pr.project_id(home)
        reg_data = {"active": None, "projects": [
            {"id": pid, "name": "home", "path": str(home), "added_at": 0}
        ]}
        import json
        reg.write_text(json.dumps(reg_data), encoding="utf-8")

        default = object()
        cache = ProjectRouterCache(default, _fake_builder([]))
        assert cache.get(pid) is default

    def test_builder_exception_falls_back_to_default(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        proj = tmp_path / "quebra"
        proj.mkdir()
        pid = pr.add_project(proj)["id"]

        def _boom(path):
            raise RuntimeError("builder quebrado")

        default = object()
        cache = ProjectRouterCache(default, _boom)
        assert cache.get(pid) is default

    def test_lru_eviction_respects_max_cached(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        pids = []
        for i in range(4):
            proj = tmp_path / f"proj{i}"
            proj.mkdir()
            pids.append(pr.add_project(proj)["id"])

        built: list = []
        default = object()
        cache = ProjectRouterCache(default, _fake_builder(built), max_cached=2)

        for pid in pids:
            cache.get(pid)
        assert len(cache) == 2  # só os 2 mais recentes ficaram

        # Reconstroi o mais antigo (foi evictado) — builder é chamado de novo.
        built.clear()
        cache.get(pids[0])
        assert built  # reconstruiu

    def test_invalidate_forces_rebuild(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        proj = tmp_path / "meu-projeto"
        proj.mkdir()
        pid = pr.add_project(proj)["id"]

        built: list = []
        default = object()
        cache = ProjectRouterCache(default, _fake_builder(built))
        cache.get(pid)
        assert len(built) == 1

        cache.invalidate(pid)
        cache.get(pid)
        assert len(built) == 2
