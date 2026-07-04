"""Testes da auto-injeção de skill (degrau 1: skills que disparam).

Cobre: match por overlap coefficient com threshold, falha-seguro no ruído,
teto de conteúdo, bloco de injeção, e as skills reais do pacote.
"""

from __future__ import annotations

from bauer.skill_match import (
    DEFAULT_THRESHOLD,
    MatchedSkill,
    match_skill,
    skill_injection_block,
    reset_cache,
)


def _docs():
    return [
        {"name": "Security Code Review",
         "description": "Revisao de codigo focada em seguranca: sql injection, authz, secrets, ssrf",
         "tags": [], "content": "Procure injection, valide entrada, cheque secrets.", "source": "builtin"},
        {"name": "Generate Tests",
         "description": "Estrategia para gerar testes: happy path, edge cases, erros e regressao",
         "tags": [], "content": "Escreva testes cobrindo caminhos felizes e de erro.", "source": "builtin"},
        {"name": "Data Analysis",
         "description": "Fluxo de analise de dados: exploracao, limpeza, visualizacao, grafico",
         "tags": [], "content": "Explore, limpe, visualize, conclua.", "source": "builtin"},
    ]


# ─── match_skill ─────────────────────────────────────────────────────────────

class TestMatchSkill:
    def test_casa_skill_certa_acima_do_threshold(self):
        m = match_skill("revise a seguranca do codigo, procure sql injection e secrets", docs=_docs())
        assert m is not None
        assert m.name == "Security Code Review"
        assert m.score >= DEFAULT_THRESHOLD

    def test_ruido_nao_casa_nada(self):
        # query sem overlap real com nenhuma skill → None (falha seguro)
        assert match_skill("qual a previsao do tempo amanha em tokyo", docs=_docs()) is None

    def test_query_vazia_retorna_none(self):
        assert match_skill("", docs=_docs()) is None
        assert match_skill("   ", docs=_docs()) is None

    def test_threshold_customizado_bloqueia(self):
        # threshold impossível → nunca injeta
        assert match_skill("seguranca sql injection secrets", docs=_docs(), threshold=1.01) is None

    def test_escolhe_maior_score(self):
        m = match_skill("gerar testes de regressao e edge cases", docs=_docs())
        assert m is not None and m.name == "Generate Tests"

    def test_skill_sem_content_nao_injeta(self):
        docs = [{"name": "Vazia", "description": "seguranca sql injection",
                 "tags": [], "content": "", "source": "builtin"}]
        # casa por descrição mas não tem conteúdo p/ injetar → None
        assert match_skill("seguranca sql injection", docs=docs) is None

    def test_content_truncado_no_teto(self):
        docs = [{"name": "Grande", "description": "analise de dados grafico",
                 "tags": [], "content": "X" * 5000, "source": "builtin"}]
        m = match_skill("analise de dados e grafico", docs=docs)
        assert m is not None and len(m.content) <= 2000

    def test_source_preservado(self):
        docs = [{"name": "MinhaSkill", "description": "seguranca sql injection authz",
                 "tags": [], "content": "guia", "source": "user"}]
        m = match_skill("seguranca sql injection", docs=docs)
        assert m is not None and m.source == "user"


# ─── skill_injection_block ───────────────────────────────────────────────────

class TestInjectionBlock:
    def test_bloco_tem_marcadores_e_conteudo(self):
        block = skill_injection_block(MatchedSkill("X", 0.5, "faca isso e aquilo", "builtin"))
        assert "<skill-relevante>" in block and "</skill-relevante>" in block
        assert "faca isso e aquilo" in block
        assert "'X'" in block


# ─── integração com as skills REAIS do pacote ────────────────────────────────

class TestRealPackageSkills:
    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_seguranca_casa_security_review(self):
        m = match_skill("revise a seguranca deste codigo, sql injection")
        assert m is not None
        assert "security" in m.name.lower()

    def test_refatorar_casa_refactor(self):
        m = match_skill("refatore este modulo python aplicando SOLID")
        assert m is not None
        assert "refactor" in m.name.lower()

    def test_ruido_puro_nao_dispara(self):
        assert match_skill("qual a capital da franca") is None
