"""Embeddings engine — semantic vector representations with graceful fallback.

Tries Ollama ``/api/embeddings`` first (requires a local embedding model such
as ``nomic-embed-text`` or ``mxbai-embed-large``).  Falls back to a TF-IDF
sparse-vector representation when Ollama is unavailable or has no embedding
model loaded.  The fallback is silent — callers always get a list[float] back.

Usage::

    from bauer.embeddings import EmbeddingEngine

    engine = EmbeddingEngine()
    v1 = engine.embed("deploy failed on kubernetes")
    v2 = engine.embed("pod crash loop back-off")

    score = EmbeddingEngine.cosine(v1, v2)
    # → 0.82 (high similarity even though words differ)

The engine auto-detects the best available backend::

    engine.backend   # "ollama" | "tfidf"
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import Counter
from typing import Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure-Python cosine similarity (no numpy dependency)
# ---------------------------------------------------------------------------


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns a value in ``[-1, 1]`` (dense) or ``[0, 1]`` (TF-IDF, non-negative).
    Returns ``0.0`` for zero vectors.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return _dot(a, b) / (na * nb)


# ---------------------------------------------------------------------------
# TF-IDF backend (zero dependencies, pure Python)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might must shall can of in on at to "
    "for with from by about and or not no nor so yet both either "
    "neither nor as if while although because since though unless until "
    "o a as de da do e em para por se um uma".split()
)

_VOCAB_SIZE = 4096  # fixed vocabulary size for TF-IDF sparse vectors

#: Intervalo mínimo entre re-tentativas de reconectar ao Ollama depois de
#: degradado para TF-IDF. Sem cooldown, cada `embed()` pagaria um timeout de
#: rede contra um servidor morto — justamente quando ele está fora do ar e
#: mais mensagens estão chegando. Medido: um processo que degrada ANTES do
#: Ollama subir travava em TF-IDF pelo resto da vida do processo (sem re-probe
#: nenhum) — este cooldown troca "nunca" por "no máximo 1x por minuto".
_REPROBE_COOLDOWN_S = 60.0


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"\b[a-záàâãéèêíìîóòôõúùûçñü_]+\b", text)
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _tfidf_vector(text: str) -> list[float]:
    """Return a fixed-length TF-IDF vector using FNV-32 hashing."""
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * _VOCAB_SIZE
    tf = Counter(tokens)
    total = len(tokens)
    vec = [0.0] * _VOCAB_SIZE
    for tok, count in tf.items():
        # FNV-32 hash for vocabulary bucketing (deterministic, no collisions needed)
        h = 2166136261
        for ch in tok.encode():
            h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
        idx = h % _VOCAB_SIZE
        # TF × log(1 + 1/(count/total)) — simple IDF approximation
        vec[idx] += (count / total) * math.log1p(total / count)
    # L2 normalise
    n = _norm(vec)
    if n > 0:
        vec = [v / n for v in vec]
    return vec


# ---------------------------------------------------------------------------
# Ollama backend probe
# ---------------------------------------------------------------------------

_OLLAMA_EMBED_MODELS = [
    "nomic-embed-text",
    "mxbai-embed-large",
    "all-minilm",
    "snowflake-arctic-embed",
    "bge-m3",
    "nomic-embed-text:latest",
]


def _probe_ollama_embeddings(base_url: str = "http://localhost:11434") -> str | None:
    """Return the name of the first available Ollama embedding model, or None."""
    try:
        import httpx
        resp = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        if resp.status_code != 200:
            return None
        tags = resp.json()
        names = {m["name"].split(":")[0] for m in tags.get("models", [])}
        for candidate in _OLLAMA_EMBED_MODELS:
            base = candidate.split(":")[0]
            if base in names:
                return candidate
    except Exception:
        pass
    return None


# Marcadores da recusa por comprimento na resposta do Ollama. Recusa por
# tamanho é CORRIGÍVEL (basta encurtar); qualquer outro erro significa que o
# servidor não está utilizável e aí o fallback é legítimo.
_ERRO_COMPRIMENTO = ("exceeds the context length", "context length", "too long")

# Quantas vezes cortar o texto pela metade antes de desistir. 3 cortes cobrem
# 8x o limite — o maior texto real medido no índice (21987 chars) entra no 2º.
_CORTES_MAX = 3


def _ollama_embed(text: str, model: str, base_url: str) -> list[float] | None:
    """Call Ollama /api/embeddings and return the vector, or None on error.

    Encurta e tenta de novo quando o servidor recusa por comprimento.

    O Ollama aplica o ``num_ctx`` padrão (2048 tokens) e não o contexto do
    modelo — o bge-m3 aceita 8192, mas na prática recusa a partir de ~6-8k
    caracteres de código/JSON. Sem este tratamento, todo texto longo caía no
    fallback TF-IDF em SILÊNCIO: o vetor saía com outra dimensão e envenenava
    um índice denso. Encurtar perde a cauda do texto, mas um embedding do
    início do documento é infinitamente melhor que um vetor de outro espaço
    vetorial — e a guarda de dimensão do vector_store recusaria a escrita de
    qualquer jeito.
    """
    try:
        import httpx
    except Exception:
        return None

    corpo = text
    for _ in range(_CORTES_MAX + 1):
        try:
            resp = httpx.post(
                f"{base_url}/api/embeddings",
                json={"model": model, "prompt": corpo},
                timeout=15.0,
            )
        except Exception:
            return None  # servidor inalcançável: fallback é legítimo
        if resp.status_code == 200:
            try:
                return resp.json().get("embedding")
            except Exception:
                return None  # 200 com corpo inválido: fallback é legítimo
        try:
            erro = str(resp.json().get("error", "")).lower()
        except Exception:
            erro = ""
        if not any(m in erro for m in _ERRO_COMPRIMENTO) or len(corpo) <= 1:
            return None  # não é problema de tamanho: fallback é legítimo
        corpo = corpo[: len(corpo) // 2]
    return None


# ---------------------------------------------------------------------------
# EmbeddingEngine
# ---------------------------------------------------------------------------


class EmbeddingEngine:
    """Embedding engine with Ollama (dense) → TF-IDF (sparse) fallback.

    Thread-safe.  The backend is auto-detected on first ``embed()`` call.

    Parameters
    ----------
    ollama_base_url:
        Base URL for the Ollama server.  ``None`` resolve via
        ``BAUER_OLLAMA_URL``, caindo para ``http://localhost:11434``.
    force_backend:
        ``"ollama"`` or ``"tfidf"`` to skip auto-detection.
    """

    def __init__(
        self,
        ollama_base_url: str | None = None,
        force_backend: str | None = None,
    ) -> None:
        # None = resolver via BAUER_OLLAMA_URL, com localhost como padrão.
        self._base_url = resolve_ollama_url(ollama_base_url).rstrip("/")
        self._force_backend = force_backend
        self._backend: str | None = force_backend  # "ollama" | "tfidf" | None (not yet detected)
        self._ollama_model: str | None = None
        self._dim: int = 0
        self._lock = threading.Lock()
        self._detected = force_backend is not None
        # force_backend="tfidf" é escolha explícita de quem chama (testes,
        # ambientes sem Ollama) — não é degradação e não merece aviso.
        self._degradacao_avisada = force_backend is not None
        # 0.0 = nunca tentou ainda; primeira chamada em TF-IDF sempre re-testa.
        self._last_probe_at: float = 0.0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def backend(self) -> str:
        """``'ollama'`` or ``'tfidf'`` — resolved after first embed()."""
        self._ensure_detected()
        return self._backend or "tfidf"

    @property
    def model(self) -> str | None:
        """Ollama embedding model in use, or ``None`` on TF-IDF."""
        self._ensure_detected()
        return self._ollama_model

    @property
    def dimension(self) -> int:
        """Vector dimension: 768-1536 for Ollama, :data:`_VOCAB_SIZE` for TF-IDF."""
        self._ensure_detected()
        return self._dim or _VOCAB_SIZE

    def embed(self, text: str) -> list[float]:
        """Return a normalized float vector for *text*.

        Never raises — falls back to TF-IDF on any Ollama error.
        """
        self._ensure_detected()
        if self._backend == "tfidf" and self._force_backend != "tfidf":
            # Degradado, mas não por escolha explícita — tenta se recuperar
            # (respeitando o cooldown) antes de aceitar TF-IDF para este turno.
            self._tentar_reprobe()
        if self._backend == "ollama" and self._ollama_model:
            vec = _ollama_embed(text, self._ollama_model, self._base_url)
            if vec is not None:
                return vec
            # Degradação em runtime: o Ollama respondia e parou. NÃO é silenciosa
            # — ver _avisar_degradacao para o porquê.
            with self._lock:
                self._backend = "tfidf"
                self._dim = _VOCAB_SIZE
                # Conta como a 1ª tentativa pro cooldown do re-probe — sem
                # isto, o PRÓXIMO embed() re-testaria na hora, pagando outro
                # timeout de rede logo depois deste ter acabado de falhar.
                self._last_probe_at = time.monotonic()
            self._avisar_degradacao(
                f"o Ollama parou de responder no meio da sessão (modelo "
                f"{self._ollama_model!r} em {self._base_url})"
            )
        return _tfidf_vector(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts.  Calls ``embed()`` for each (no batching API)."""
        return [self.embed(t) for t in texts]

    @staticmethod
    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        """Cosine similarity between two vectors. Alias for :func:`cosine_similarity`."""
        return cosine_similarity(a, b)

    def rank(
        self,
        query: str,
        candidates: list[str],
        *,
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Return the top-k (index, score) pairs from *candidates* by similarity to *query*.

        Example::

            engine = EmbeddingEngine()
            ranked = engine.rank("deploy failure", ["build ok", "pod crash", "lint pass"])
            # [(1, 0.87), (0, 0.23), (2, 0.11)]
        """
        if not candidates:
            return []
        q_vec = self.embed(query)
        scored = [
            (i, cosine_similarity(q_vec, self.embed(c)))
            for i, c in enumerate(candidates)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _avisar_degradacao(self, motivo: str) -> None:
        """Avisa UMA vez que o engine está em TF-IDF em vez de embedding denso.

        Por que isto existe: a degradação era silenciosa por design, e o custo
        disso foi medido em 2026-08-04 — o índice do Bauer tinha 5.957 vetores
        que *pareciam* semânticos e eram TF-IDF, porque a URL do Ollama apontava
        para um host onde ele não estava. Ninguém percebeu, porque nada avisava:
        a busca continuava "funcionando", só que devolvendo lista vazia para
        qualquer pergunta escrita com outras palavras (TF-IDF dá similaridade
        0.000 para paráfrase — não "pior", zero).

        Um aviso por transição, não por chamada: `embed()` roda por mensagem
        indexada e encheria o log. E não é "uma vez por processo" porque, se um
        dia o engine voltar a subir para o Ollama, uma nova queda merece um novo
        aviso.
        """
        # Sempre chamado FORA do lock (nem embed() nem _ensure_detected() o
        # seguram aqui), então adquirir é seguro e evita aviso duplicado.
        with self._lock:
            if self._degradacao_avisada:
                return
            self._degradacao_avisada = True
        log.warning(
            "embeddings: usando TF-IDF (busca por palavra-chave) em vez de "
            "embedding semântico — %s. Efeito: perguntas escritas com "
            "palavras diferentes do texto indexado deixam de encontrá-lo "
            "(similaridade 0.000 para paráfrase). Para corrigir: instale o "
            "Ollama (https://ollama.com/download), rode `ollama pull bge-m3` "
            "e reinicie o Bauer — a reindexação do que ficou pra trás "
            "acontece sozinha depois disso, sem mais nenhum comando. "
            "Diagnóstico completo: `bauer doctor`.",
            motivo,
        )

    def _ensure_detected(self) -> None:
        if self._detected:
            return
        with self._lock:
            if self._detected:
                return
            self._detected = True
            if self._force_backend == "tfidf":
                self._backend = "tfidf"
                self._dim = _VOCAB_SIZE
                return
            model = _probe_ollama_embeddings(self._base_url)
            if model:
                # Verify it actually works with a short probe text
                vec = _ollama_embed("test", model, self._base_url)
                if vec and len(vec) > 0:
                    self._backend = "ollama"
                    self._ollama_model = model
                    self._dim = len(vec)
                    return
                motivo = (
                    f"o modelo {model!r} foi encontrado em {self._base_url} mas "
                    f"não devolveu embedding utilizável"
                )
            else:
                motivo = (
                    f"nenhum modelo de embedding encontrado em {self._base_url} "
                    f"(procurados: {', '.join(_OLLAMA_EMBED_MODELS[:3])}…)"
                )
            self._backend = "tfidf"
            self._dim = _VOCAB_SIZE
            # Conta como a 1ª tentativa: sem isto, o 1º embed() logo em
            # seguida re-testaria de novo na hora, duplicando o custo da
            # detecção inicial que acabou de falhar.
            self._last_probe_at = time.monotonic()
        # Fora do lock: logging pode ser lento e não precisa de exclusão mútua.
        self._avisar_degradacao(motivo)

    def _tentar_reprobe(self) -> None:
        """Retesta o Ollama depois de degradado, no máximo 1x por cooldown.

        Sem isto, um processo que degradou ANTES do Ollama subir (ou durante
        um blip transitório) ficava preso em TF-IDF pelo resto da vida do
        processo — nunca havia novo teste. Silencioso quando não recupera
        (o motivo já foi avisado por `_avisar_degradacao`); avisa quando
        recupera, porque a troca de volta também é uma mudança de estado que
        quem lê o log precisa saber.
        """
        agora = time.monotonic()
        with self._lock:
            if agora - self._last_probe_at < _REPROBE_COOLDOWN_S:
                return
            self._last_probe_at = agora
        model = _probe_ollama_embeddings(self._base_url)
        if not model:
            return
        vec = _ollama_embed("test", model, self._base_url)
        if not (vec and len(vec) > 0):
            return
        with self._lock:
            self._backend = "ollama"
            self._ollama_model = model
            self._dim = len(vec)
            # Recuperou: uma queda FUTURA é uma transição nova e merece um
            # aviso novo, não silêncio por já ter avisado uma vez no passado.
            self._degradacao_avisada = False
        log.warning(
            "embeddings: Ollama voltou a responder em %s — engine recuperado "
            "para embedding semântico (modelo %r, %d dims). Mensagens gravadas "
            "durante a degradação continuam em TF-IDF; rode "
            "VectorStore.rebuild_index() para reindexá-las.",
            self._base_url, model, len(vec),
        )


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

#: Shared EmbeddingEngine for the whole process.
#: Import and call directly::
#:
#:     from bauer.embeddings import default_engine
#:     vec = default_engine.embed("some text")
default_engine: EmbeddingEngine | None = None
_engine_lock = threading.Lock()


def resolve_ollama_url(explicito: str | None = None) -> str:
    """URL do Ollama: argumento explícito > ``BAUER_OLLAMA_URL`` > localhost.

    Existe por dois motivos concretos:

    * **Ollama remoto.** O servidor pode não estar na mesma máquina (GPU num
      host separado). Sem isto, a única saída era um túnel SSH para fazer o
      ``localhost`` fixo apontar para o lugar certo.
    * **Hermeticidade da suíte.** Com a URL fixa, um dev com Ollama acessível
      exercitava um caminho diferente do CI: o backend virava ``ollama`` e a
      busca semântica passava a devolver resultado onde o TF-IDF devolvia
      lista vazia (embedding denso dá similaridade não-nula para QUALQUER
      par de textos). Isso derrubava
      ``test_sqlite_session_store::test_search_no_match`` de forma
      intermitente — parecia flake, era ambiente vazando para dentro do
      teste. O ``tests/conftest.py`` aponta esta variável para um endereço
      morto e o probe falha rápido, igual a CI limpo.
    """
    if explicito:
        return explicito
    return os.environ.get("BAUER_OLLAMA_URL") or "http://localhost:11434"


def get_default_engine(ollama_base_url: str | None = None) -> EmbeddingEngine:
    """Return the shared :class:`EmbeddingEngine`, creating it on first call."""
    global default_engine
    if default_engine is None:
        with _engine_lock:
            if default_engine is None:
                default_engine = EmbeddingEngine(
                    ollama_base_url=resolve_ollama_url(ollama_base_url)
                )
    return default_engine
