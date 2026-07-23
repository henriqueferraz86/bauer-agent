"""Characterization: paridade entre as 2 gerações de task-store (spike #10).

Contexto (auditoria 023, achado #10): o Bauer tem DUAS gerações de
armazenamento de tarefas coexistindo:

  * Gen 1 (legado, DEFAULT do mainline): `WorkspaceManager` → `TASKS.md`
    (markdown). Usado por agent/dispatcher/execution/gateways.
  * Gen 2 (kernel SQLite): `kanban_db` + `WorkspaceManagerSqlite` (drop-in
    com a MESMA API pública e o MESMO dataclass `Task`). Usado pela superfície
    nova (swarm/decompose/specify/boards/daemon).

A ponte já existe e está ligada: `bauer kanban-migrate` (`migrate_tasks_md`),
idempotente. Estes testes NÃO migram nada em produção — eles PINAM o contrato
de paridade que uma virada de default dependeria, e documentam as
assimetrias conhecidas como características explícitas (não como bugs a
"corrigir"). Se algum destes quebrar no futuro, a virada ficou mais arriscada.

Escopo deliberado: só a superfície pública compartilhada pelas duas APIs
(add/list/get/update_status/update_metadata/comment). Métodos exclusivos de
uma geração (runs sidecar do md; CAS/claim/boards do db) ficam de fora — são
cobertos pelos testes próprios de cada módulo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bauer.workspace_manager import Task, WorkspaceManager
from bauer.workspace_manager_sqlite import WorkspaceManagerSqlite


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isola BAUER_HOME (onde o kanban_db grava os boards) por teste."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def wm_md(tmp_path: Path) -> WorkspaceManager:
    """Gen 1 — WorkspaceManager sobre TASKS.md real."""
    wm = WorkspaceManager(tmp_path / "ws_md")
    wm.init_project("Projeto")
    return wm


@pytest.fixture
def wm_sql(tmp_path: Path, bauer_home: Path) -> WorkspaceManagerSqlite:
    """Gen 2 — WorkspaceManagerSqlite sobre kanban_db (board default)."""
    wm = WorkspaceManagerSqlite(tmp_path / "ws_sql")
    wm.init_project("Projeto")
    return wm


# ---------------------------------------------------------------------------
# 1. Paridade direta da API pública (drop-in equivalence)
# ---------------------------------------------------------------------------


class TestDirectApiParity:
    """Mesma sequência de chamadas → mesmos campos observáveis no `Task`."""

    def test_add_task_returns_same_shape(self, wm_md, wm_sql):
        t_md = wm_md.add_task("Otimizar query", description="usar índices",
                              status="READY", priority="high", assignee="ana")
        t_sql = wm_sql.add_task("Otimizar query", description="usar índices",
                                status="READY", priority="high", assignee="ana")
        # Ambas retornam o MESMO dataclass Task.
        assert isinstance(t_md, Task) and isinstance(t_sql, Task)
        # ID sequencial zero-padded começa em "001" nas duas.
        assert t_md.id == t_sql.id == "001"
        for field in ("title", "description", "status", "priority", "assignee"):
            assert getattr(t_md, field) == getattr(t_sql, field), field

    def test_sequential_ids_match(self, wm_md, wm_sql):
        for i in range(1, 4):
            a = wm_md.add_task(f"t{i}")
            b = wm_sql.add_task(f"t{i}")
            assert a.id == b.id == str(i).zfill(3)

    def test_default_status_differs_documented(self, wm_md, wm_sql):
        # DIVERGÊNCIA conhecida: default do add_task não é o mesmo.
        #   md  → "READY"   (workspace_manager.add_task)
        #   sql → "TODO"    (workspace_manager_sqlite.add_task)
        # Pinado para que uma virada saiba reconciliar o default.
        assert wm_md.add_task("x").status == "READY"
        assert wm_sql.add_task("x").status == "TODO"

    def test_update_status_parity(self, wm_md, wm_sql):
        wm_md.add_task("t"); wm_sql.add_task("t")
        assert wm_md.update_task_status("001", "IN_PROGRESS").status == "IN_PROGRESS"
        assert wm_sql.update_task_status("001", "IN_PROGRESS").status == "IN_PROGRESS"

    def test_comment_roundtrips_in_both(self, wm_md, wm_sql):
        wm_md.add_task("t"); wm_sql.add_task("t")
        tm = wm_md.add_task_comment("001", "primeiro comentário")
        ts = wm_sql.add_task_comment("001", "primeiro comentário")
        assert any("primeiro comentário" == c.get("text") for c in tm.comments)
        assert any("primeiro comentário" == c.get("text") for c in ts.comments)


# ---------------------------------------------------------------------------
# 2. Fidelidade da migração ponta-a-ponta (o caminho REAL da virada)
# ---------------------------------------------------------------------------


class TestMigrationRoundTripParity:
    """WorkspaceManager (md) real → kanban-migrate → WorkspaceManagerSqlite.

    É exatamente o que `bauer kanban-migrate` faz. Prova que os campos que a
    API antiga expõe sobrevivem à virada.
    """

    def _migrate(self, tasks_md: Path, board=None):
        from bauer.kanban_migration import migrate_tasks_md
        return migrate_tasks_md(tasks_md, board=board)

    def test_core_fields_survive_migration(self, tmp_path, bauer_home):
        # 1. Cria via WorkspaceManager real (como o agente faz hoje).
        src = WorkspaceManager(tmp_path / "ws")
        src.init_project("Projeto")
        src.add_task("Refatorar módulo X", description="quebrar em 3 arquivos",
                     status="IN_PROGRESS", priority="high", assignee="bob")

        # 2. Migra TASKS.md → kanban_db (board default).
        report = self._migrate(src.tasks_file)
        assert report.total >= 1

        # 3. Lê via a API drop-in (o que a virada usaria).
        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        t = dst.get_task("001")
        assert t.title == "Refatorar módulo X"
        assert t.description == "quebrar em 3 arquivos"
        assert t.priority == "high"
        assert t.assignee == "bob"
        assert t.status == "IN_PROGRESS"           # IN_PROGRESS → running → IN_PROGRESS

    @pytest.mark.xfail(
        strict=True,
        reason="ACHADO #10-A: WorkspaceManager.add_task_comment escreve "
               "'comment: <ts> | autor | texto', mas kanban_migration.read_tasks_md "
               "só reconhece bullets Markdown '- ' → o comentário vaza para a "
               "description em vez de virar comentário. A migração precisa aprender "
               "o formato 'comment:' ANTES de qualquer virada de default. "
               "(xfail strict: quando alguém corrigir, este teste XPASSA e falha, "
               "sinalizando p/ remover o marcador.)",
    )
    def test_comment_survives_migration(self, tmp_path, bauer_home):
        src = WorkspaceManager(tmp_path / "ws")
        src.init_project("Projeto")
        src.add_task("Tarefa", description="corpo limpo")
        src.add_task_comment("001", "comentário de contexto")

        self._migrate(src.tasks_file)
        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        t = dst.get_task("001")
        # Contrato desejado (hoje QUEBRADO): comentário vira comentário, e a
        # description não é poluída pela linha 'comment:'.
        assert t.description == "corpo limpo"
        assert any("comentário de contexto" == c.get("text") for c in t.comments)

    def test_ids_preserved_through_migration(self, tmp_path, bauer_home):
        src = WorkspaceManager(tmp_path / "ws")
        src.init_project("Projeto")
        for i in range(1, 4):
            src.add_task(f"tarefa {i}")
        self._migrate(src.tasks_file)
        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        ids = {t.id for t in dst.list_tasks()}
        assert {"001", "002", "003"} <= ids

    def test_migration_is_idempotent(self, tmp_path, bauer_home):
        src = WorkspaceManager(tmp_path / "ws")
        src.init_project("Projeto")
        src.add_task("única")
        self._migrate(src.tasks_file)
        self._migrate(src.tasks_file)   # 2ª vez não duplica
        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        assert len([t for t in dst.list_tasks() if t.title == "única"]) == 1

    def test_all_md_statuses_roundtrip_losslessly(self, tmp_path, bauer_home):
        # Os 6 status do md sobrevivem à ida-e-volta pela db sem perda.
        src = WorkspaceManager(tmp_path / "ws")
        src.init_project("Projeto")
        for st in ("TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"):
            src.add_task(f"task {st}", status=st)
        self._migrate(src.tasks_file)
        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        by_title = {t.title: t.status for t in dst.list_tasks()}
        for st in ("TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"):
            assert by_title[f"task {st}"] == st, st


# ---------------------------------------------------------------------------
# 3. Assimetria conhecida: db→API md é LOSSY (risco documentado da virada)
# ---------------------------------------------------------------------------


class TestStatusMappingIsLossy:
    """kanban_db tem 9 status; a API md-compatível expõe só 6.

    Status nativos do kanban_db (triage/review/archived) — que a superfície
    nova (swarm/specify) SETA — colapsam quando lidos pela API drop-in. Um
    consumidor legado NÃO os distingue. Este é o risco central de uma virada
    "só trocar o backend" sem alinhar o vocabulário de status.
    """

    def test_native_db_statuses_collapse_to_md_vocab(self, tmp_path, bauer_home):
        from bauer import kanban_db as kb

        with kb.connect() as conn:
            kb.init_db(conn)
            # create_task já abre seu próprio write_txn (BEGIN IMMEDIATE) —
            # não envolver em outra transação (aninhamento é erro no SQLite).
            kb.create_task(conn, "em triagem", status=kb.STATUS_TRIAGE, task_id="010")
            kb.create_task(conn, "em review", status=kb.STATUS_REVIEW, task_id="011")
            kb.create_task(conn, "arquivada", status=kb.STATUS_ARCHIVED, task_id="012")

        dst = WorkspaceManagerSqlite(tmp_path / "ws_view")
        collapsed = {t.id: t.status for t in dst.list_tasks()}
        # LOSSY — os 3 nativos viram TODO/IN_PROGRESS/DONE:
        assert collapsed["010"] == "TODO"        # triage  → TODO
        assert collapsed["011"] == "IN_PROGRESS" # review  → IN_PROGRESS
        assert collapsed["012"] == "DONE"        # archived → DONE
