"""Testes da App Factory — Spec-Driven Development gravado no DNA do Bauer.

Cobre: derivação de gates, scaffold idempotente, hash pristino, can_write_code,
Delivery Score objetivo, e o enforcement via ToolRouter.execute().
"""

from __future__ import annotations

import json

import pytest

from bauer import app_factory as af


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill(project, name, text="conteudo real preenchido " * 20):
    (project / "docs" / name).write_text(f"# {name}\n{text}", encoding="utf-8")


def _fill_all_planning(project):
    for d in af.PLANNING_DOCS:
        _fill(project, d)


# ---------------------------------------------------------------------------
# Governança / marker
# ---------------------------------------------------------------------------


class TestGovernance:
    def test_not_governed_by_default(self, tmp_path):
        assert af.is_governed(tmp_path) is False
        assert af.current_gate(tmp_path) is None

    def test_init_creates_marker_and_docs(self, tmp_path):
        res = af.init_project(tmp_path, idea="Encurtador de URLs", stack="FastAPI")
        assert af.is_governed(tmp_path) is True
        assert (tmp_path / "docs" / af.MARKER_NAME).is_file()
        # 7 planning + 6 delivery + README/.env.example/CI
        assert len(res["written"]) >= 13
        for d in af.PLANNING_DOCS:
            assert (tmp_path / "docs" / d).is_file()

    def test_state_records_idea_and_stack(self, tmp_path):
        af.init_project(tmp_path, idea="Minha ideia", stack="Next.js")
        st = af.load_state(tmp_path)
        assert st["idea"] == "Minha ideia"
        assert st["stack"] == "Next.js"
        assert "pristine_hashes" in st


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestGates:
    def test_discovery_after_init(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.current_gate(tmp_path) == af.Gate.DISCOVERY

    def test_planning_after_spec_filled(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md")
        assert af.current_gate(tmp_path) == af.Gate.PLANNING

    def test_implementation_after_all_planning_docs(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill_all_planning(tmp_path)
        assert af.planning_complete(tmp_path) is True
        assert af.current_gate(tmp_path) >= af.Gate.IMPLEMENTATION

    def test_scaffolded_skeleton_does_not_count_as_filled(self, tmp_path):
        # Só o esqueleto não preenche — gate continua em discovery.
        af.init_project(tmp_path, idea="x")
        assert af.doc_is_filled(tmp_path, "ARCHITECTURE.md") is False
        assert af.current_gate(tmp_path) == af.Gate.DISCOVERY

    def test_missing_planning_docs_listed(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md")
        missing = af.missing_planning_docs(tmp_path)
        assert "SPEC.md" not in missing
        assert "ARCHITECTURE.md" in missing


# ---------------------------------------------------------------------------
# can_write_code (enforcement lógico)
# ---------------------------------------------------------------------------


class TestCanWriteCode:
    def test_ungoverned_allows_everything(self, tmp_path):
        ok, _ = af.can_write_code(tmp_path, "app/main.py")
        assert ok is True

    def test_governed_blocks_code_in_discovery(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        ok, reason = af.can_write_code(tmp_path, "app/main.py")
        assert ok is False
        assert "planejamento" in reason.lower()

    def test_governed_allows_docs(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        ok, _ = af.can_write_code(tmp_path, "docs/SPEC.md")
        assert ok is True

    def test_governed_allows_readme_and_env(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.can_write_code(tmp_path, "README.md")[0] is True
        assert af.can_write_code(tmp_path, ".env.example")[0] is True
        assert af.can_write_code(tmp_path, ".github/workflows/ci.yml")[0] is True

    def test_code_allowed_after_planning_complete(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill_all_planning(tmp_path)
        ok, _ = af.can_write_code(tmp_path, "app/main.py")
        assert ok is True


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


class TestScaffold:
    def test_idempotent_without_overwrite(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md", text="EDITADO PELO USUARIO " * 20)
        # re-scaffold sem overwrite não apaga edição
        af.scaffold_docs(tmp_path, idea="x", overwrite=False)
        content = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "EDITADO PELO USUARIO" in content

    def test_overwrite_restores_skeleton(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        _fill(tmp_path, "SPEC.md", text="EDITADO " * 20)
        af.scaffold_docs(tmp_path, idea="x", overwrite=True)
        content = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "EDITADO" not in content

    def test_pristine_hash_detects_edit(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        assert af.doc_is_filled(tmp_path, "SPEC.md") is False
        _fill(tmp_path, "SPEC.md")
        assert af.doc_is_filled(tmp_path, "SPEC.md") is True

    def test_idea_injected_into_spec(self, tmp_path):
        af.init_project(tmp_path, idea="Plataforma de cursos online")
        spec = (tmp_path / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "Plataforma de cursos online" in spec


# ---------------------------------------------------------------------------
# Delivery Score
# ---------------------------------------------------------------------------


class TestDeliveryScore:
    def test_low_score_right_after_init(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        sc = af.delivery_score(tmp_path)
        # README/.env.example scaffoldados contam; docs ainda não preenchidos
        assert sc["score"] < af.DELIVERY_READY_THRESHOLD
        assert sc["ready"] is False
        assert sc["total"] == len(sc["checks"])

    def test_score_rises_when_docs_filled(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        before = af.delivery_score(tmp_path)["score"]
        _fill_all_planning(tmp_path)
        _fill(tmp_path, "SECURITY_CHECKLIST.md")
        _fill(tmp_path, "DEPLOY_CHECKLIST.md")
        _fill(tmp_path, "RUNBOOK.md")
        after = af.delivery_score(tmp_path)["score"]
        assert after > before

    def test_tests_signal_detected(self, tmp_path):
        af.init_project(tmp_path, idea="x")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_smoke.py").write_text("def test_x(): assert True", encoding="utf-8")
        assert af.delivery_score(tmp_path)["checks"]["tests"] is True

    def test_verified_false_sem_arquivo(self, tmp_path):
        """P1.4: verified=False quando verify_result.json não existe."""
        af.init_project(tmp_path, idea="y")
        sc = af.delivery_score(tmp_path)
        assert "verified" in sc["checks"]
        assert sc["checks"]["verified"] is False

    def test_verified_true_quando_ok(self, tmp_path):
        """P1.4: verified=True quando verify_result.json existe com ok=True."""
        import json
        af.init_project(tmp_path, idea="y")
        meta = tmp_path / ".bauer_meta"
        meta.mkdir(exist_ok=True)
        (meta / "verify_result.json").write_text(
            json.dumps({"ok": True, "stack": "node", "summary": "✓"}), encoding="utf-8"
        )
        assert af.delivery_score(tmp_path)["checks"]["verified"] is True

    def test_verified_false_quando_falhou(self, tmp_path):
        """P1.4: verified=False quando verify_result.json existe com ok=False."""
        import json
        af.init_project(tmp_path, idea="y")
        meta = tmp_path / ".bauer_meta"
        meta.mkdir(exist_ok=True)
        (meta / "verify_result.json").write_text(
            json.dumps({"ok": False, "stack": "node", "summary": "✗"}), encoding="utf-8"
        )
        assert af.delivery_score(tmp_path)["checks"]["verified"] is False

    def test_score_sobe_com_verified(self, tmp_path):
        """P1.4: score aumenta quando verify passa."""
        import json
        af.init_project(tmp_path, idea="y")
        score_sem = af.delivery_score(tmp_path)["score"]
        meta = tmp_path / ".bauer_meta"
        meta.mkdir(exist_ok=True)
        (meta / "verify_result.json").write_text(
            json.dumps({"ok": True, "stack": "python"}), encoding="utf-8"
        )
        score_com = af.delivery_score(tmp_path)["score"]
        assert score_com > score_sem


# ---------------------------------------------------------------------------
# Integração com ToolRouter (enforcement no DNA)
# ---------------------------------------------------------------------------


class TestToolRouterIntegration:
    def _router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_init_tool_starts_governance(self, tmp_path):
        tr = self._router(tmp_path)
        out = tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Encurtador", "stack": "FastAPI", "path": "encurtador"},
        }))
        assert "app_factory_init" in out
        assert af.is_governed(tmp_path / "encurtador")
        assert not af.is_governed(tmp_path)  # a raiz NUNCA é governada

    def test_init_nao_forca_as_4_perguntas_clarify(self, tmp_path):
        # Discovery agora é rascunho da IA + premissas, não interrogatório.
        # A instrução NÃO deve mandar chamar clarify 4x; deve orientar a
        # preencher os docs da ideia e marcar premissas.
        tr = self._router(tmp_path)
        out = tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Encurtador de URLs", "path": "enc"},
        }))
        low = out.lower()
        assert "quatro vezes" not in low
        assert "clarify" in low  # ainda menciona clarify (uso condicional)
        assert "premissa" in low  # orienta marcar premissas
        assert "rascunh" in low  # IA rascunha, não interroga

    def test_init_requires_idea(self, tmp_path):
        from bauer.tool_router import ToolError
        tr = self._router(tmp_path)
        with pytest.raises(ToolError):
            tr.execute(json.dumps({"action": "app_factory_init", "args": {}}))

    def test_init_requires_path(self, tmp_path):
        # Sem path o init caía na raiz do workspace (compartilhada) e herdava
        # estado de projetos anteriores — agora é recusado com orientação.
        from bauer.tool_router import ToolError
        tr = self._router(tmp_path)
        with pytest.raises(ToolError, match="path"):
            tr.execute(json.dumps({
                "action": "app_factory_init", "args": {"idea": "BauerInvest"},
            }))
        assert not af.is_governed(tmp_path)

    def test_init_rejects_workspace_root_as_path(self, tmp_path):
        from bauer.tool_router import ToolError
        tr = self._router(tmp_path)
        for bad in (".", "./", "sub/.."):
            with pytest.raises(ToolError):
                tr.execute(json.dumps({
                    "action": "app_factory_init",
                    "args": {"idea": "BauerInvest", "path": bad},
                }))
        assert not af.is_governed(tmp_path)

    def test_write_code_blocked_before_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init", "args": {"idea": "x", "path": "meu-app"},
        }))
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "meu-app/app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" in out
        assert not (tmp_path / "meu-app" / "app" / "main.py").exists()

    def test_write_doc_allowed_before_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init", "args": {"idea": "x", "path": "meu-app"},
        }))
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "meu-app/docs/NOTAS.md", "content": "# notas\n" + "x" * 50},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "meu-app" / "docs" / "NOTAS.md").exists()

    def test_write_code_allowed_after_planning(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init", "args": {"idea": "x", "path": "meu-app"},
        }))
        _fill_all_planning(tmp_path / "meu-app")
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "meu-app/app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "meu-app" / "app" / "main.py").exists()

    def test_ungoverned_project_unaffected(self, tmp_path):
        # Sem init: comportamento normal do Bauer, sem bloqueio.
        tr = self._router(tmp_path)
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "app" / "main.py").exists()

    def test_status_and_score_tools(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init", "args": {"idea": "x", "path": "meu-app"},
        }))
        # Sem path, status/score resolvem o PROJETO ATIVO (não a raiz).
        status_out = tr.execute(json.dumps({"action": "app_factory_status", "args": {}}))
        assert "gate: discovery" in status_out
        score_out = tr.execute(json.dumps({"action": "app_factory_score", "args": {}}))
        assert "Delivery Score" in score_out


# ---------------------------------------------------------------------------
# Templates / skill
# ---------------------------------------------------------------------------


def test_all_templates_present():
    names = set(af.list_templates())
    for d in (*af.PLANNING_DOCS, *af.DELIVERY_DOCS):
        assert d in names, f"template {d} ausente"
    assert "README.md" in names
    assert ".env.example" in names


def test_skill_yaml_discoverable():
    from pathlib import Path
    p = Path(af.__file__).parent / "data" / "skills" / "coding" / "app-factory.yaml"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "app_factory_init" in text


# ---------------------------------------------------------------------------
# Projeto ativo + containment (1 ideia = 1 pasta; nada solto na raiz)
# ---------------------------------------------------------------------------


class TestActiveProjectContainment:
    def test_set_get_active_project(self, tmp_path):
        proj = tmp_path / "minha-ideia"
        proj.mkdir()
        assert af.get_active_project(tmp_path) is None  # nenhum ativo no início
        af.set_active_project(tmp_path, proj)
        active = af.get_active_project(tmp_path)
        assert active is not None and active.name == "minha-ideia"

    def test_active_pointer_ignora_pasta_inexistente(self, tmp_path):
        af.set_active_project(tmp_path, tmp_path / "some-proj")  # nunca criada
        assert af.get_active_project(tmp_path) is None  # defensivo

    def test_containment_noop_sem_projeto_ativo(self, tmp_path):
        ok, _ = af.check_containment(tmp_path, "qualquer/coisa.py")
        assert ok is True

    def test_containment_permite_dentro_bloqueia_fora(self, tmp_path):
        proj = tmp_path / "problemas-app"
        proj.mkdir()
        af.set_active_project(tmp_path, proj)
        assert af.check_containment(tmp_path, "problemas-app/app/main.py")[0] is True
        # fora: pasta irmã e raiz solta
        ok_sibling, why = af.check_containment(tmp_path, "real-problems-mapper/app.py")
        assert ok_sibling is False and "problemas-app" in why
        assert af.check_containment(tmp_path, "README_solto.md")[0] is False

    def test_init_subpasta_seta_ativo_e_contem(self, tmp_path):
        from bauer.tool_router import ToolRouter
        tr = ToolRouter(workspace=tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Mapear problemas", "path": "problemas-app"},
        }))
        # projeto ativo fixado na subpasta
        active = af.get_active_project(tmp_path)
        assert active is not None and active.name == "problemas-app"

        # escrever FORA da pasta da ideia → bloqueado por containment
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "real-problems-mapper/app/main.py", "content": "x"},
        }))
        assert "[App Factory]" in out and "projeto ativo" in out
        assert not (tmp_path / "real-problems-mapper" / "app" / "main.py").exists()

        # doc DENTRO da pasta da ideia → liberado (always-writable)
        out2 = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "problemas-app/docs/NOTAS.md", "content": "# n\n" + "x" * 60},
        }))
        assert "[App Factory]" not in out2
        assert (tmp_path / "problemas-app" / "docs" / "NOTAS.md").exists()

    def test_containment_message_orienta_projeto_novo(self, tmp_path):
        # Cenário real: ponteiro ativo aponta pro projeto ANTERIOR e o usuário
        # pede um app NOVO — a mensagem de bloqueio deve orientar o modelo a
        # chamar app_factory_init com path novo, não a enfiar tudo na pasta velha.
        proj = tmp_path / "nexusalpha"
        proj.mkdir()
        af.set_active_project(tmp_path, proj)
        ok, why = af.check_containment(tmp_path, "bauerinvest/app/main.py")
        assert ok is False
        assert "app_factory_init" in why
        assert "projeto NOVO" in why

    def test_init_subpasta_libera_codigo_dentro_apos_planning(self, tmp_path):
        from bauer.tool_router import ToolRouter
        tr = ToolRouter(workspace=tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "x", "path": "problemas-app"},
        }))
        _fill_all_planning(tmp_path / "problemas-app")
        out = tr.execute(json.dumps({
            "action": "write_file",
            "args": {"path": "problemas-app/app/main.py", "content": "print(1)"},
        }))
        assert "[App Factory]" not in out
        assert (tmp_path / "problemas-app" / "app" / "main.py").exists()


# ---------------------------------------------------------------------------
# guard_reinit — regras anti-clobber (nunca sobrescrever projeto existente)
# ---------------------------------------------------------------------------


class TestGuardReinit:
    def test_pasta_nao_governada_sempre_permite(self, tmp_path):
        ok, why = af.guard_reinit(tmp_path, idea="qualquer")
        assert ok is True and why == ""

    def test_mesma_ideia_esqueleto_permite_retomada(self, tmp_path):
        af.init_project(tmp_path, idea="Encurtador de URLs")
        ok, _ = af.guard_reinit(tmp_path, idea="Encurtador de URLs")
        assert ok is True

    def test_mesma_ideia_normalizada_permite(self, tmp_path):
        # Comparação tolera caixa e espaços — não bloqueia retomada legítima.
        af.init_project(tmp_path, idea="Encurtador  de URLs")
        ok, _ = af.guard_reinit(tmp_path, idea="encurtador de urls")
        assert ok is True

    def test_outra_ideia_sem_overwrite_bloqueia(self, tmp_path):
        af.init_project(tmp_path, idea="Plataforma de saude mental")
        ok, why = af.guard_reinit(tmp_path, idea="BauerInvest investimentos")
        assert ok is False
        assert "OUTRA ideia" in why
        assert "overwrite" in why

    def test_outra_ideia_com_overwrite_permite_se_so_esqueleto(self, tmp_path):
        af.init_project(tmp_path, idea="Ideia velha")
        ok, _ = af.guard_reinit(tmp_path, idea="Ideia nova", overwrite=True)
        assert ok is True

    def test_projeto_completo_outra_ideia_bloqueia_sempre(self, tmp_path):
        af.init_project(tmp_path, idea="Ideia velha")
        _fill_all_planning(tmp_path)
        for ow in (False, True):
            ok, why = af.guard_reinit(tmp_path, idea="Ideia nova", overwrite=ow)
            assert ok is False
            assert "COMPLETO" in why

    def test_projeto_completo_mesma_ideia_overwrite_bloqueia(self, tmp_path):
        # overwrite re-scaffoldaria os docs preenchidos → destruição. Nunca.
        af.init_project(tmp_path, idea="Ideia")
        _fill_all_planning(tmp_path)
        ok, why = af.guard_reinit(tmp_path, idea="Ideia", overwrite=True)
        assert ok is False and "COMPLETO" in why

    def test_projeto_completo_mesma_ideia_sem_overwrite_permite_noop(self, tmp_path):
        af.init_project(tmp_path, idea="Ideia")
        _fill_all_planning(tmp_path)
        ok, _ = af.guard_reinit(tmp_path, idea="Ideia", overwrite=False)
        assert ok is True  # idempotente: scaffold sem overwrite não toca nada

    def test_overwrite_atualiza_ideia_no_estado(self, tmp_path):
        af.init_project(tmp_path, idea="Ideia velha")
        af.init_project(tmp_path, idea="Ideia nova", overwrite=True)
        st = af.load_state(tmp_path)
        assert st["idea"] == "Ideia nova"

    def test_reinit_sem_overwrite_preserva_ideia(self, tmp_path):
        af.init_project(tmp_path, idea="Ideia original")
        af.init_project(tmp_path, idea="")  # init sem ideia não apaga a original
        assert af.load_state(tmp_path)["idea"] == "Ideia original"


class TestGuardReinitViaRouter:
    """Cenário do incidente real: modelo tenta init na pasta de projeto anterior."""

    def _router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_init_em_pasta_de_outro_projeto_bloqueado(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "NexusAlpha plataforma de investimentos",
                     "path": "nexusalpha"},
        }))
        out = tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "BauerInvest recomendacoes de investimentos",
                     "path": "nexusalpha"},
        }))
        assert "BLOQUEADO" in out
        # a ideia original ficou intacta
        st = af.load_state(tmp_path / "nexusalpha")
        assert "NexusAlpha" in st["idea"]

    def test_init_em_projeto_completo_bloqueado_mesmo_com_overwrite(self, tmp_path):
        tr = self._router(tmp_path)
        tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Projeto pronto", "path": "pronto-app"},
        }))
        _fill_all_planning(tmp_path / "pronto-app")
        out = tr.execute(json.dumps({
            "action": "app_factory_init",
            "args": {"idea": "Projeto novo", "path": "pronto-app",
                     "overwrite": True},
        }))
        assert "BLOQUEADO" in out and "COMPLETO" in out
        spec = (tmp_path / "pronto-app" / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "conteudo real preenchido" in spec  # docs preenchidos intactos
