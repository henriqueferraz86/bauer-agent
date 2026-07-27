"""Testes da auto-injeção de skill (degrau 1: skills que disparam).

Cobre: match por overlap coefficient com threshold, falha-seguro no ruído,
teto de conteúdo, bloco de injeção, e as skills reais do pacote.
"""

from __future__ import annotations

from bauer.skill_match import (
    DEFAULT_THRESHOLD,
    MatchedSkill,
    _CONTENT_CAP,
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
                 "tags": [], "content": "X" * (_CONTENT_CAP + 5000), "source": "builtin"}]
        m = match_skill("analise de dados e grafico", docs=docs)
        assert m is not None and len(m.content) == _CONTENT_CAP

    def test_skill_grande_cabe_inteira(self):
        # Skill do tamanho da app-factory (~4.8k) não pode chegar decapitada:
        # metade de um gate de spec-driven é pior que gate nenhum.
        content = "PASSO 1\n" + ("x" * 4800) + "\nPASSO FINAL"
        docs = [{"name": "Grande", "description": "analise de dados grafico",
                 "tags": [], "content": content, "source": "builtin"}]
        m = match_skill("analise de dados e grafico", docs=docs)
        assert m is not None and m.content.endswith("PASSO FINAL")

    def test_empate_prefere_nome_mais_coberto_pela_query(self):
        # Mesmo score (ambos saturam em 1.0): vence o nome sem qualificador que a
        # query não pediu — e não a ordem de leitura do diretório.
        generica = {"name": "Code Review", "description": "revisao de codigo em dois eixos",
                    "tags": [], "content": "guia generico", "source": "builtin"}
        estreita = {"name": "Security Code Review", "description": "revisao de codigo focada em seguranca",
                    "tags": [], "content": "guia de seguranca", "source": "builtin"}
        for ordem in ([estreita, generica], [generica, estreita]):
            m = match_skill("faz um code review dessa branch", docs=ordem)
            assert m is not None and m.name == "Code Review"

    def test_query_especifica_ainda_casa_skill_estreita(self):
        # O desempate não pode sequestrar o pedido específico: dizendo "security",
        # a cobertura do nome estreito passa a ser total.
        generica = {"name": "Code Review", "description": "revisao de codigo em dois eixos",
                    "tags": [], "content": "guia generico", "source": "builtin"}
        estreita = {"name": "Security Code Review", "description": "revisao de codigo focada em seguranca",
                    "tags": [], "content": "guia de seguranca", "source": "builtin"}
        m = match_skill("security code review dessa branch", docs=[generica, estreita])
        assert m is not None and m.name == "Security Code Review"

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

    def test_docker_casa_docker_ops(self):
        m = match_skill("faça uma analise docker")
        assert m is not None
        assert "docker" in m.name.lower()

    def test_ruido_puro_nao_dispara(self):
        assert match_skill("qual a capital da franca") is None
