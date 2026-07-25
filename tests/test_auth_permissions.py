"""Permissões dos arquivos de credencial do TokenStore (plano 007).

A chave Fernet que cifra TODOS os tokens OAuth era gravada com o umask padrão
(022) → 0644, world-readable, e LADO A LADO com o auth.json que ela protege.
Qualquer outro usuário da máquina lia os dois e decriptava tudo: Fernet +
PBKDF2 100k anulados por uma permissão de arquivo.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from bauer.auth import AuthToken, TokenStore


# chmod POSIX não existe no Windows — lá a ACL do perfil já restringe ~/.bauer,
# e o _restringir() é no-op deliberado. Os testes de bits só fazem sentido em
# POSIX; os de comportamento (abaixo) rodam em todo lugar.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="permissões POSIX não se aplicam no Windows"
)


def _modo(caminho) -> int:
    return stat.S_IMODE(os.stat(caminho).st_mode)


@posix_only
def test_auth_key_criada_com_0600(tmp_path):
    store = TokenStore(base_dir=tmp_path)
    key_file = tmp_path / ".auth_key"
    assert key_file.exists()
    assert _modo(key_file) == 0o600, (
        f"chave em {oct(_modo(key_file))}: outro usuário local lê o segredo "
        "que decripta todos os tokens OAuth"
    )
    assert store._obfuscation_key


@posix_only
def test_auth_key_legada_e_corrigida_na_leitura(tmp_path):
    """Instalação anterior ao fix já tem a chave 0644 no disco — abrir o store
    tem que consertar, não só acertar arquivos novos."""
    key_file = tmp_path / ".auth_key"
    key_file.write_text("chave-legada-em-plaintext")
    os.chmod(key_file, 0o644)
    assert _modo(key_file) == 0o644  # pré-condição

    store = TokenStore(base_dir=tmp_path)

    assert _modo(key_file) == 0o600
    assert store._obfuscation_key == "chave-legada-em-plaintext"


@posix_only
def test_diretorio_base_e_0700(tmp_path):
    base = tmp_path / ".bauer"
    TokenStore(base_dir=base)
    assert _modo(base) == 0o700


@posix_only
def test_auth_json_gravado_com_0600(tmp_path):
    store = TokenStore(base_dir=tmp_path)
    store.save(AuthToken(provider="openai", access_token="sk-segredo"))
    assert _modo(tmp_path / "auth.json") == 0o600


# --- comportamento (roda em qualquer SO) ------------------------------------


def test_chave_nao_e_derivada_de_machine_id(tmp_path):
    """O docstring antigo dizia "baseada no machine ID" — é `secrets.token_hex`
    aleatório. Duas instalações na MESMA máquina têm chaves diferentes."""
    a = TokenStore(base_dir=tmp_path / "a")._obfuscation_key
    b = TokenStore(base_dir=tmp_path / "b")._obfuscation_key
    assert a != b
    assert len(a) == 32  # token_hex(16)


def test_chave_e_estavel_entre_aberturas(tmp_path):
    """Reabrir o store tem que reusar a chave — senão os tokens já gravados
    ficam indecriptáveis."""
    primeira = TokenStore(base_dir=tmp_path)._obfuscation_key
    segunda = TokenStore(base_dir=tmp_path)._obfuscation_key
    assert primeira == segunda


def test_roundtrip_do_token_continua_funcionando(tmp_path):
    """O chmod não pode quebrar o caminho normal de salvar/carregar."""
    store = TokenStore(base_dir=tmp_path)
    store.save(AuthToken(provider="openai", access_token="sk-abc123", refresh_token="rt-xyz"))

    recarregado = TokenStore(base_dir=tmp_path).load("openai")
    assert recarregado is not None
    assert recarregado.access_token == "sk-abc123"
    assert recarregado.refresh_token == "rt-xyz"


def test_token_nao_fica_em_plaintext_no_disco(tmp_path):
    """Confere a premissa que justifica proteger a chave: o auth.json é mesmo
    cifrado (senão a permissão seria o único controle)."""
    store = TokenStore(base_dir=tmp_path)
    store.save(AuthToken(provider="openai", access_token="sk-valor-secreto-unico"))
    bruto = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "sk-valor-secreto-unico" not in bruto


def test_chmod_indisponivel_nao_derruba_o_store(tmp_path, monkeypatch):
    """Filesystems sem chmod POSIX (FAT, montagens de rede): falhar o login por
    causa de um chmod seria pior que o risco que ele mitiga."""
    def _explode(*a, **k):
        raise OSError("chmod não suportado")

    monkeypatch.setattr(os, "chmod", _explode)
    store = TokenStore(base_dir=tmp_path)
    store.save(AuthToken(provider="openai", access_token="sk-ok"))
    assert TokenStore(base_dir=tmp_path).load("openai").access_token == "sk-ok"


def test_chmod_e_chamado_com_os_modos_certos(tmp_path, monkeypatch):
    """Verifica a INTENÇÃO em qualquer SO.

    Os testes de bits acima são POSIX-only e ficam pulados no Windows — ou
    seja, metade da matriz do CI não exercitaria a regra. Aqui captura-se o
    que foi pedido ao os.chmod, o que vale em toda plataforma.
    """
    chamadas: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def _captura(caminho, modo, *a, **k):
        chamadas.append((os.path.basename(str(caminho)), modo))
        try:
            real_chmod(caminho, modo, *a, **k)
        except OSError:
            pass

    monkeypatch.setattr(os, "chmod", _captura)

    store = TokenStore(base_dir=tmp_path / ".bauer")
    store.save(AuthToken(provider="openai", access_token="sk-x"))

    por_arquivo = dict(chamadas)
    assert por_arquivo.get(".auth_key") == 0o600, f"chamadas: {chamadas}"
    assert por_arquivo.get("auth.json") == 0o600, f"chamadas: {chamadas}"
    assert por_arquivo.get(".bauer") == 0o700, f"chamadas: {chamadas}"
