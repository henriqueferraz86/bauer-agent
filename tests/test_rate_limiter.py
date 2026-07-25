"""Testes para _RateLimiter do servidor HTTP."""

from __future__ import annotations

import time
from unittest.mock import patch

from bauer.server import _RateLimiter


def test_allows_requests_within_limit():
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    for _ in range(5):
        assert limiter.is_allowed("192.168.1.1") is True


def test_blocks_after_limit_exceeded():
    limiter = _RateLimiter(max_requests=3, window_s=60.0)
    for _ in range(3):
        limiter.is_allowed("10.0.0.1")
    assert limiter.is_allowed("10.0.0.1") is False


def test_different_ips_are_independent():
    limiter = _RateLimiter(max_requests=2, window_s=60.0)
    limiter.is_allowed("1.1.1.1")
    limiter.is_allowed("1.1.1.1")
    assert limiter.is_allowed("1.1.1.1") is False
    # IP diferente ainda tem budget
    assert limiter.is_allowed("2.2.2.2") is True


def test_disabled_when_max_requests_zero():
    limiter = _RateLimiter(max_requests=0, window_s=60.0)
    for _ in range(1000):
        assert limiter.is_allowed("any-ip") is True


def test_window_resets_after_expiry():
    """Requisições antigas saem da janela e o IP volta a ser permitido."""
    limiter = _RateLimiter(max_requests=2, window_s=0.1)  # janela de 100ms
    limiter.is_allowed("ip-a")
    limiter.is_allowed("ip-a")
    assert limiter.is_allowed("ip-a") is False

    time.sleep(0.15)  # espera janela expirar
    assert limiter.is_allowed("ip-a") is True


def test_retry_after_positive_when_blocked():
    limiter = _RateLimiter(max_requests=1, window_s=60.0)
    limiter.is_allowed("blocked-ip")
    assert limiter.is_allowed("blocked-ip") is False
    retry = limiter.retry_after("blocked-ip")
    assert 0 < retry <= 60.0


def test_retry_after_zero_for_unknown_ip():
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    assert limiter.retry_after("new-ip") == 0.0


def test_sliding_window_partial_expiry():
    """Apenas requisições fora da janela são removidas — não todas."""
    limiter = _RateLimiter(max_requests=3, window_s=0.2)

    # 2 requisições agora
    limiter.is_allowed("ip-x")
    limiter.is_allowed("ip-x")

    time.sleep(0.15)  # essas 2 ainda estão na janela

    # 1 requisição mais recente — total 3 → no limite
    assert limiter.is_allowed("ip-x") is True  # 3a → OK (no limite)
    assert limiter.is_allowed("ip-x") is False  # 4a → bloqueado

    time.sleep(0.1)  # as 2 primeiras expiram (0.15 + 0.1 = 0.25 > 0.2)
    # Agora só a 3a ainda está na janela → budget de 2 disponível
    assert limiter.is_allowed("ip-x") is True
    assert limiter.is_allowed("ip-x") is True
    assert limiter.is_allowed("ip-x") is False


# ─── P0-3: X-Forwarded-For nao validado ──────────────────────────────────────
#
# `_get_client_ip` confiava no header incondicionalmente. Como ele e escolhido
# pelo CLIENTE, bastava varia-lo a cada request para ganhar um bucket novo: o
# limite nunca disparava, e o dict de buckets crescia sem teto. O limitador
# virava o proprio vetor de DoS.

from bauer.server import _client_ip_from, _parse_trusted_proxies  # noqa: E402


def _tp(entries):
    return _parse_trusted_proxies(entries)


def test_xff_ignorado_sem_trusted_proxies():
    """Default seguro: sem lista configurada o header nao vale nada."""
    redes, coringa = _tp([])
    ip = _client_ip_from("203.0.113.9", "1.2.3.4", redes, coringa)
    assert ip == "203.0.113.9", "XFF forjado nao pode virar a chave do rate limit"


def test_xff_honrado_vindo_de_proxy_confiavel():
    redes, coringa = _tp(["10.0.0.0/8"])
    ip = _client_ip_from("10.1.2.3", "203.0.113.9", redes, coringa)
    assert ip == "203.0.113.9"


def test_xff_ignorado_de_peer_fora_da_lista():
    redes, coringa = _tp(["10.0.0.0/8"])
    ip = _client_ip_from("198.51.100.7", "203.0.113.9", redes, coringa)
    assert ip == "198.51.100.7"


def test_xff_pega_o_primeiro_nao_confiavel_da_direita():
    """O cliente pode enviar o PROPRIO XFF; o proxy apenas ANEXA o peer. Pegar
    o item mais a esquerda (comportamento antigo) seria spoofavel mesmo atras
    de proxy confiavel. Caminha-se da direita ate o 1o nao-confiavel."""
    redes, coringa = _tp(["10.0.0.0/8"])
    # Cliente forjou "1.2.3.4"; a parte real da cadeia e 203.0.113.9.
    ip = _client_ip_from("10.0.0.1", "1.2.3.4, 203.0.113.9, 10.0.0.5", redes, coringa)
    assert ip == "203.0.113.9", "pegou o valor forjado pelo cliente"


def test_coringa_confia_em_qualquer_peer():
    redes, coringa = _tp(["*"])
    assert _client_ip_from("198.51.100.7", "203.0.113.9", redes, coringa) == "203.0.113.9"


def test_cadeia_toda_confiavel_cai_no_peer():
    redes, coringa = _tp(["10.0.0.0/8"])
    assert _client_ip_from("10.0.0.1", "10.0.0.2, 10.0.0.3", redes, coringa) == "10.0.0.1"


def test_entradas_invalidas_nao_derrubam_o_boot():
    redes, coringa = _tp(["nao-e-ip", "", "10.0.0.0/8", "999.999.999.999"])
    assert coringa is False
    assert _client_ip_from("10.0.0.1", "203.0.113.9", redes, coringa) == "203.0.113.9"


def test_peer_sem_socket_nao_quebra():
    redes, coringa = _tp([])
    assert _client_ip_from("", "", redes, coringa) == "unknown"


def test_bypass_do_limite_com_xff_forjado():
    """Regressao ponta a ponta: 100 requests variando o XFF, todas do MESMO
    peer, tem que bater no limite. Antes, cada header inventado abria um
    bucket novo e o limite jamais disparava."""
    redes, coringa = _tp([])
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    permitidas = 0
    for i in range(100):
        chave = _client_ip_from("203.0.113.9", f"10.0.0.{i % 256}", redes, coringa)
        if limiter.is_allowed(chave):
            permitidas += 1
    assert permitidas == 5, f"limite contornado: {permitidas} passaram de 5"
    assert limiter.tracked_keys() == 1


# ─── P0-3: crescimento ilimitado do dict de buckets ──────────────────────────


def test_buckets_expirados_sao_removidos():
    """`is_allowed` esvaziava a deque de uma chave ociosa mas nunca removia a
    CHAVE — o dict so crescia."""
    limiter = _RateLimiter(max_requests=5, window_s=0.05)
    for i in range(500):
        limiter.is_allowed(f"ip-{i}")
    assert limiter.tracked_keys() == 500
    time.sleep(0.1)  # tudo expira
    limiter._prune_locked(time.monotonic())
    assert limiter.tracked_keys() == 0


def test_teto_de_chaves_impede_exaustao_de_memoria():
    """Chaves ATIVAS acima do teto sao negadas em vez de crescerem o dict."""
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    limiter._MAX_KEYS = 50
    for i in range(500):
        limiter.is_allowed(f"ip-{i}")
    assert limiter.tracked_keys() <= 50


def test_negado_ao_estourar_o_teto_e_nao_silenciosamente_permitido():
    """Fail-closed: e um limitador, errar para o lado restritivo."""
    limiter = _RateLimiter(max_requests=99, window_s=60.0)
    limiter._MAX_KEYS = 3
    for i in range(3):
        assert limiter.is_allowed(f"ativo-{i}") is True
    assert limiter.is_allowed("chave-nova") is False


# ─── P0-3: thread-safety (o docstring afirmava, o codigo nao tinha lock) ─────


def test_limite_respeitado_sob_concorrencia():
    """FastAPI roda endpoints `def` (nao `async def`) no threadpool. Sem lock,
    `len(window) >= max` seguido de `append` e read-modify-write: duas threads
    leem o mesmo tamanho abaixo do limite e ambas passam.

    Threads normais NAO expoem isso: a secao critica e curta demais e o GIL a
    serializa por acidente, entao o teste passaria com e sem lock — falsa
    confianca. Alarga-se a janela de propósito com uma deque cujo `append`
    dorme: com lock as threads serializam e o total fica exato; sem lock,
    varias passam pela checagem enquanto outras dormem no append.
    """
    import threading
    from collections import deque as _deque

    class _DequeLenta(_deque):
        def append(self, item):
            time.sleep(0.001)   # alarga a janela entre checar e inserir
            super().append(item)

    limiter = _RateLimiter(max_requests=50, window_s=60.0)
    limiter._windows["mesma-chave"] = _DequeLenta()

    passaram = []
    trava = threading.Lock()

    def bate():
        if limiter.is_allowed("mesma-chave"):
            with trava:
                passaram.append(1)

    threads = [threading.Thread(target=bate) for _ in range(300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(passaram) == 50, f"limite furado sob concorrencia: {len(passaram)} passaram de 50"
