"""Tests for bauer/embeddings.py — EmbeddingEngine + TF-IDF fallback."""

from __future__ import annotations

import math
import pytest

from bauer.embeddings import (
    EmbeddingEngine,
    cosine_similarity,
    _tfidf_vector,
    _tokenize,
    _VOCAB_SIZE,
    get_default_engine,
)


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_zero_vector():
    a = [0.0, 0.0]
    b = [1.0, 2.0]
    assert cosine_similarity(a, b) == 0.0


def test_cosine_empty_vectors():
    assert cosine_similarity([], []) == 0.0


def test_cosine_mismatched_lengths():
    # Should return 0.0 not raise
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_range():
    import random
    rng = random.Random(42)
    for _ in range(20):
        a = [rng.uniform(-1, 1) for _ in range(10)]
        b = [rng.uniform(-1, 1) for _ in range(10)]
        score = cosine_similarity(a, b)
        assert -1.001 <= score <= 1.001, f"Out of range: {score}"


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


def test_tokenize_basic():
    tokens = _tokenize("The quick brown fox")
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens
    # "The" should be filtered (stop word) or lowercased
    assert "The" not in tokens
    assert "the" not in tokens  # stop word


def test_tokenize_punctuation_stripped():
    tokens = _tokenize("hello, world! python3.11")
    assert "hello" in tokens
    assert "world" in tokens


def test_tokenize_empty():
    assert _tokenize("") == []


def test_tokenize_only_stopwords():
    tokens = _tokenize("the is a an of")
    # All are stop words — result should be empty or near-empty
    for tok in tokens:
        assert len(tok) > 1  # short stop words removed


# ---------------------------------------------------------------------------
# _tfidf_vector
# ---------------------------------------------------------------------------


def test_tfidf_vector_length():
    v = _tfidf_vector("hello world python")
    assert len(v) == _VOCAB_SIZE


def test_tfidf_vector_normalized():
    v = _tfidf_vector("machine learning models train test evaluate")
    norm = math.sqrt(sum(x * x for x in v))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_tfidf_vector_empty_text():
    v = _tfidf_vector("")
    assert len(v) == _VOCAB_SIZE
    assert all(x == 0.0 for x in v)


def test_tfidf_similar_texts_closer_than_different():
    v1 = _tfidf_vector("kubernetes pod crash restart error")
    v2 = _tfidf_vector("kubernetes container restart failure")
    v3 = _tfidf_vector("apple pie recipe baking flour")
    sim_related = cosine_similarity(v1, v2)
    sim_unrelated = cosine_similarity(v1, v3)
    assert sim_related > sim_unrelated


def test_tfidf_deterministic():
    text = "deploy kubernetes pod failure restart"
    v1 = _tfidf_vector(text)
    v2 = _tfidf_vector(text)
    assert v1 == v2


# ---------------------------------------------------------------------------
# EmbeddingEngine — TF-IDF backend
# ---------------------------------------------------------------------------


@pytest.fixture
def tfidf_engine():
    """EmbeddingEngine forced to TF-IDF (no Ollama probe)."""
    return EmbeddingEngine(force_backend="tfidf")


def test_engine_backend_tfidf(tfidf_engine):
    assert tfidf_engine.backend == "tfidf"


def test_engine_dimension_tfidf(tfidf_engine):
    assert tfidf_engine.dimension == _VOCAB_SIZE


def test_engine_embed_returns_list_of_floats(tfidf_engine):
    v = tfidf_engine.embed("hello world")
    assert isinstance(v, list)
    assert all(isinstance(x, float) for x in v)
    assert len(v) == _VOCAB_SIZE


def test_engine_embed_never_raises(tfidf_engine):
    # Even with empty text, should return zero vector, not raise
    v = tfidf_engine.embed("")
    assert len(v) == _VOCAB_SIZE


def test_engine_embed_batch(tfidf_engine):
    texts = ["first text", "second text", "third"]
    vecs = tfidf_engine.embed_batch(texts)
    assert len(vecs) == 3
    assert all(len(v) == _VOCAB_SIZE for v in vecs)


def test_engine_cosine_static(tfidf_engine):
    a = tfidf_engine.embed("dog cat animal")
    b = tfidf_engine.embed("feline canine pets")
    score = EmbeddingEngine.cosine(a, b)
    assert 0.0 <= score <= 1.0


def test_engine_rank(tfidf_engine):
    query = "kubernetes pod failure"
    candidates = [
        "apple pie recipe",
        "kubernetes container crash restart",
        "kubernetes pod error",
        "baking bread flour yeast",
    ]
    ranked = tfidf_engine.rank(query, candidates, top_k=2)
    assert len(ranked) == 2
    # The kubernetes-related items should rank first
    top_idx = ranked[0][0]
    assert top_idx in (1, 2)  # "crash restart" or "pod error"


def test_engine_rank_empty_candidates(tfidf_engine):
    ranked = tfidf_engine.rank("anything", [])
    assert ranked == []


def test_engine_rank_respects_top_k(tfidf_engine):
    candidates = ["a", "b", "c", "d", "e"]
    ranked = tfidf_engine.rank("test", candidates, top_k=3)
    assert len(ranked) <= 3


# ---------------------------------------------------------------------------
# EmbeddingEngine — thread safety
# ---------------------------------------------------------------------------


def test_engine_thread_safe_embed():
    import threading
    engine = EmbeddingEngine(force_backend="tfidf")
    results: list[list[float]] = []
    errors: list[Exception] = []

    def _embed():
        try:
            v = engine.embed("concurrent embedding test text")
            results.append(v)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_embed) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert len(results) == 10
    # All embeddings should be identical (same text)
    assert all(results[i] == results[0] for i in range(1, 10))


# ---------------------------------------------------------------------------
# get_default_engine
# ---------------------------------------------------------------------------


def test_get_default_engine_returns_engine():
    engine = get_default_engine()
    assert isinstance(engine, EmbeddingEngine)


def test_get_default_engine_singleton():
    e1 = get_default_engine()
    e2 = get_default_engine()
    assert e1 is e2


# ---------------------------------------------------------------------------
# _ollama_embed — recusa por comprimento vs. servidor indisponível
#
# O Ollama aplica num_ctx=2048 e não o contexto do modelo, então texto longo
# volta 500 "exceeds the context length". Antes, isso era tratado igual a
# "servidor sumiu" e o engine caía para TF-IDF em SILÊNCIO — gravando um vetor
# de outra dimensão no índice denso. Encurtar e repetir é o conserto; errar a
# distinção entre os dois tipos de erro é o que causava o envenenamento.
# ---------------------------------------------------------------------------


class _RespostaFake:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _instala_post_fake(monkeypatch, handler):
    """Troca httpx.post e devolve a lista de tamanhos de prompt recebidos."""
    import httpx

    tamanhos: list[int] = []

    def _post(url, json=None, timeout=None, **kwargs):
        tamanhos.append(len(json["prompt"]))
        return handler(json["prompt"])

    monkeypatch.setattr(httpx, "post", _post)
    return tamanhos


def test_ollama_embed_encurta_quando_excede_contexto(monkeypatch):
    from bauer.embeddings import _ollama_embed

    limite = 100

    def handler(prompt: str):
        if len(prompt) > limite:
            return _RespostaFake(500, {"error": "the input length exceeds the context length"})
        return _RespostaFake(200, {"embedding": [0.5] * 1024})

    tamanhos = _instala_post_fake(monkeypatch, handler)
    vec = _ollama_embed("x" * 800, "bge-m3", "http://fake")

    assert vec is not None and len(vec) == 1024
    # Cortou pela metade a cada tentativa até caber: 800 -> 400 -> 200 -> 100.
    assert tamanhos == [800, 400, 200, 100]


def test_ollama_embed_nao_encurta_em_erro_generico(monkeypatch):
    from bauer.embeddings import _ollama_embed

    def handler(prompt: str):
        return _RespostaFake(500, {"error": "model not found"})

    tamanhos = _instala_post_fake(monkeypatch, handler)
    assert _ollama_embed("x" * 800, "bge-m3", "http://fake") is None
    # Erro que não é de tamanho: uma tentativa só, fallback é legítimo.
    assert tamanhos == [800]


def test_ollama_embed_desiste_apos_cortes_maximos(monkeypatch):
    from bauer.embeddings import _CORTES_MAX, _ollama_embed

    def handler(prompt: str):
        return _RespostaFake(500, {"error": "the input length exceeds the context length"})

    tamanhos = _instala_post_fake(monkeypatch, handler)
    assert _ollama_embed("x" * 800, "bge-m3", "http://fake") is None
    # Não entra em laço infinito: limitado a _CORTES_MAX cortes.
    assert len(tamanhos) == _CORTES_MAX + 1


def test_ollama_embed_none_quando_servidor_inalcancavel(monkeypatch):
    import httpx

    from bauer.embeddings import _ollama_embed

    chamadas: list[int] = []

    def _post(url, json=None, timeout=None, **kwargs):
        chamadas.append(1)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _post)
    assert _ollama_embed("texto", "bge-m3", "http://fake") is None
    assert len(chamadas) == 1  # sem retry: o servidor não está lá


def test_ollama_embed_sucesso_direto_nao_encurta(monkeypatch):
    from bauer.embeddings import _ollama_embed

    def handler(prompt: str):
        return _RespostaFake(200, {"embedding": [0.1] * 1024})

    tamanhos = _instala_post_fake(monkeypatch, handler)
    vec = _ollama_embed("texto normal", "bge-m3", "http://fake")

    assert vec is not None and len(vec) == 1024
    assert tamanhos == [len("texto normal")]  # texto preservado inteiro


class _Resposta200JsonInvalido:
    """Simula um 200 cujo corpo não é JSON válido (proxy, stream truncado)."""

    def __init__(self) -> None:
        self.status_code = 200

    def json(self):
        raise ValueError("corpo nao e JSON valido")


def test_ollama_embed_200_com_corpo_invalido_retorna_none(monkeypatch):
    from bauer.embeddings import _ollama_embed

    tamanhos = _instala_post_fake(monkeypatch, lambda prompt: _Resposta200JsonInvalido())
    assert _ollama_embed("texto", "bge-m3", "http://fake") is None
    # 200 não é erro de tamanho: uma tentativa só, sem retry.
    assert tamanhos == [len("texto")]


def test_engine_embed_nao_propaga_excecao_com_200_invalido(monkeypatch):
    """embed() deve honrar 'nunca lança' mesmo quando o servidor devolve 200 quebrado."""
    from bauer.embeddings import EmbeddingEngine
    import bauer.embeddings as mod

    _instala_post_fake(monkeypatch, lambda prompt: _Resposta200JsonInvalido())

    engine = EmbeddingEngine()
    # Simula detecção bem-sucedida, sem tocar na rede (mesmo padrão do teste
    # de degradação em runtime acima).
    engine._detected = True
    engine._backend = "ollama"
    engine._ollama_model = "bge-m3"
    engine._dim = 1024

    vec = engine.embed("texto")  # não deve lançar

    assert len(vec) == mod._VOCAB_SIZE  # caiu para TF-IDF
    assert engine.backend == "tfidf"


# ---------------------------------------------------------------------------
# resolve_ollama_url — Ollama remoto + hermeticidade da suíte
# ---------------------------------------------------------------------------


def test_resolve_ollama_url_prefere_argumento_explicito(monkeypatch):
    from bauer.embeddings import resolve_ollama_url

    monkeypatch.setenv("BAUER_OLLAMA_URL", "http://do-ambiente:11434")
    assert resolve_ollama_url("http://explicito:11434") == "http://explicito:11434"


def test_resolve_ollama_url_usa_variavel_de_ambiente(monkeypatch):
    from bauer.embeddings import resolve_ollama_url

    monkeypatch.setenv("BAUER_OLLAMA_URL", "http://beelink:11434")
    assert resolve_ollama_url() == "http://beelink:11434"


def test_resolve_ollama_url_cai_para_localhost(monkeypatch):
    from bauer.embeddings import resolve_ollama_url

    monkeypatch.delenv("BAUER_OLLAMA_URL", raising=False)
    assert resolve_ollama_url() == "http://localhost:11434"


def test_resolve_ollama_url_ignora_variavel_vazia(monkeypatch):
    from bauer.embeddings import resolve_ollama_url

    monkeypatch.setenv("BAUER_OLLAMA_URL", "")
    assert resolve_ollama_url() == "http://localhost:11434"


def test_engine_respeita_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("BAUER_OLLAMA_URL", "http://beelink:11434/")
    engine = EmbeddingEngine(force_backend="tfidf")
    assert engine._base_url == "http://beelink:11434"  # barra final removida


# ---------------------------------------------------------------------------
# Aviso de degradação
#
# A degradação era silenciosa "por design" e o custo foi medido: 5.957 vetores
# que pareciam semânticos e eram TF-IDF, por anos, porque nada avisava.
# ---------------------------------------------------------------------------


def test_avisa_quando_ollama_nao_esta_disponivel(caplog):
    import logging

    # URL morta: o probe falha e o engine cai para TF-IDF.
    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        engine.embed("qualquer texto")

    assert engine.backend == "tfidf"
    assert any("TF-IDF" in r.getMessage() for r in caplog.records)


def test_aviso_de_degradacao_sai_uma_vez_so(caplog):
    import logging

    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        for _ in range(5):
            engine.embed("texto repetido")

    avisos = [r for r in caplog.records if "TF-IDF" in r.getMessage()]
    assert len(avisos) == 1  # por transição, não por chamada


def test_force_backend_tfidf_nao_avisa(caplog):
    """Escolha explícita de quem chama não é degradação."""
    import logging

    engine = EmbeddingEngine(force_backend="tfidf")
    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        engine.embed("texto")

    assert not [r for r in caplog.records if "TF-IDF" in r.getMessage()]


def test_avisa_quando_ollama_cai_no_meio(monkeypatch, caplog):
    """Degradação em runtime: respondia, parou de responder."""
    import logging

    import bauer.embeddings as mod

    engine = EmbeddingEngine()
    # Simula detecção bem-sucedida, sem tocar na rede.
    engine._detected = True
    engine._backend = "ollama"
    engine._ollama_model = "bge-m3"
    engine._dim = 1024

    monkeypatch.setattr(mod, "_ollama_embed", lambda *a, **k: None)  # servidor sumiu
    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        vec = engine.embed("texto")

    assert len(vec) == mod._VOCAB_SIZE  # caiu para TF-IDF
    assert engine.backend == "tfidf"
    assert any("parou de responder" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Re-probe com cooldown
#
# Gap medido em uso real: um processo cujo EmbeddingEngine degradou para
# TF-IDF (Ollama fora do ar na primeira detecção, ou caiu no meio) ficava
# preso nisso pelo resto da vida do processo — sem re-probe algum, mesmo com
# o Ollama voltando a responder minutos depois. Só um restart do processo
# resolvia. Corrigido: re-teste automático, limitado por cooldown.
# ---------------------------------------------------------------------------


def test_reprobe_recupera_para_ollama_quando_volta(monkeypatch):
    import bauer.embeddings as mod

    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    engine.embed("primeira mensagem")  # degrada; _last_probe_at fica setado
    assert engine.backend == "tfidf"

    # Ollama "volta" — simula sem tocar rede de verdade.
    monkeypatch.setattr(mod, "_probe_ollama_embeddings", lambda base_url: "bge-m3")
    monkeypatch.setattr(mod, "_ollama_embed", lambda *a, **k: [0.1] * 1024)
    # Pula o cooldown manualmente — em uso real isto é o tempo passando.
    engine._last_probe_at = 0.0

    vec = engine.embed("segunda mensagem, depois da recuperacao")
    assert engine.backend == "ollama"
    assert len(vec) == 1024


def test_reprobe_respeita_cooldown_nao_retesta_toda_chamada(monkeypatch):
    import bauer.embeddings as mod

    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    engine.embed("primeira mensagem")
    assert engine.backend == "tfidf"

    chamadas = []
    monkeypatch.setattr(mod, "_probe_ollama_embeddings",
                        lambda base_url: chamadas.append(1) or None)

    for _ in range(10):
        engine.embed("mensagem repetida ainda dentro do cooldown")

    assert chamadas == [], "cooldown ainda não passou — não devia re-testar"


def test_reprobe_avisa_recuperacao_no_log(monkeypatch, caplog):
    import logging

    import bauer.embeddings as mod

    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    engine.embed("primeira mensagem")

    monkeypatch.setattr(mod, "_probe_ollama_embeddings", lambda base_url: "bge-m3")
    monkeypatch.setattr(mod, "_ollama_embed", lambda *a, **k: [0.1] * 1024)
    engine._last_probe_at = 0.0

    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        engine.embed("mensagem apos recuperacao")

    assert any("voltou a responder" in r.getMessage() for r in caplog.records)


def test_reprobe_reseta_aviso_para_proxima_queda(monkeypatch, caplog):
    """Recuperar zera `_degradacao_avisada` — uma queda FUTURA merece aviso
    novo, não silêncio por já ter avisado uma vez, meses atrás."""
    import logging

    import bauer.embeddings as mod

    engine = EmbeddingEngine(ollama_base_url="http://127.0.0.1:1")
    engine.embed("primeira mensagem")  # avisa 1x

    monkeypatch.setattr(mod, "_probe_ollama_embeddings", lambda base_url: "bge-m3")
    monkeypatch.setattr(mod, "_ollama_embed", lambda *a, **k: [0.1] * 1024)
    engine._last_probe_at = 0.0
    engine.embed("recupera")
    assert engine.backend == "ollama"

    # Cai de novo — precisa avisar de novo, não ficar mudo.
    monkeypatch.setattr(mod, "_ollama_embed", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING, logger="bauer.embeddings"):
        engine.embed("cai de novo")

    avisos = [r for r in caplog.records if "parou de responder" in r.getMessage()]
    assert len(avisos) == 1


def test_force_backend_tfidf_nunca_reprobe(monkeypatch):
    """Escolha explícita não é degradação — não deve tentar reconectar."""
    import bauer.embeddings as mod

    chamadas = []
    monkeypatch.setattr(mod, "_probe_ollama_embeddings",
                        lambda base_url: chamadas.append(1) or None)

    engine = EmbeddingEngine(force_backend="tfidf")
    for _ in range(3):
        engine.embed("texto")

    assert chamadas == []
    assert engine.backend == "tfidf"


def test_suite_e_hermetica_quanto_ao_ollama():
    """O conftest tem de apontar a URL para um endereço morto.

    Sem isto, quem tem Ollama rodando exercita um caminho diferente do CI e
    testes de "não acha nada" viram flake.
    """
    import os

    assert os.environ.get("BAUER_OLLAMA_URL") == "http://127.0.0.1:1"
