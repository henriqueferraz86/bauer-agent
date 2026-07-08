# Plan 004: Eliminar o fallback XOR silencioso em auth.py

> **Instruções ao executor**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição de STOP ocorrer, pare e reporte — não improvise.
> Ao terminar, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 820322b..HEAD -- bauer/auth.py tests/test_auth.py`
> Se `bauer/auth.py` mudou na área de `_encrypt_token` / `_xor_encrypt`, compare
> os trechos "Estado atual" com o código ao vivo antes de prosseguir.

## Status

- **Prioridade**: P1
- **Esforço**: S
- **Risco**: LOW
- **Depende de**: nenhum
- **Categoria**: security
- **Planejado em**: commit `820322b`, 2026-06-27

## Por que isso importa

Quando a biblioteca `cryptography` não está instalada, `_encrypt_token()` cai
silenciosamente em XOR com chave repetida. XOR com chave repetida é
criptoanálise básica: prefixos conhecidos dos tokens (`sk-`, `Bearer `,
`fernet:`) permitem recuperar a chave por análise de frequência. O problema
não é "XOR é fraco" — é que o usuário não sabe que a segurança degradou.

A `cryptography` está listada como dependência de desenvolvimento (`[dev]`)
mas **não** como dependência principal (`dependencies`). Isso significa que
em uma instalação de produção com `pip install bauer-agent` sem extras, a
`cryptography` pode não estar presente — e tokens OAuth são silenciosamente
reduzidos a XOR.

A correção correta: tornar o fallback XOR um erro explícito, e adicionar
`cryptography` como dependência core (ou ao menos do extra `[server]`, que
é o que `bauer serve` usa e que expõe o servidor com tokens OAuth ativos).

## Estado atual

Arquivo relevante: `bauer/auth.py`

```python
# bauer/auth.py:175-206

def _try_get_fernet(raw_key: str):
    """Retorna objeto Fernet ou None se cryptography não instalado."""
    try:
        from cryptography.fernet import Fernet
        key = _derive_fernet_key(raw_key)
        return Fernet(key)
    except ImportError:
        return None   # ← aqui: falha silenciosa


def _xor_encrypt(token: str, key: str) -> str:
    """XOR legacy — mantido como fallback."""
    import base64
    encrypted = bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(token.encode()))
    return base64.b64encode(encrypted).decode()


def _encrypt_token(token: str, key: str) -> str:
    """Encripta token com Fernet (AES-CBC + HMAC) ou XOR como fallback."""
    if not key or not token:
        return token
    fernet = _try_get_fernet(key)
    if fernet is not None:
        return _FERNET_PREFIX + fernet.encrypt(token.encode()).decode()
    return _xor_encrypt(token, key)   # ← fallback silencioso para XOR
```

`pyproject.toml` relevante:

```toml
# pyproject.toml:13-21
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "psutil>=5.9",
    "prompt-toolkit>=3.0",
]
# cryptography NÃO está aqui — só em [keychain] e [dev]

[project.optional-dependencies]
keychain = [
    "keyring>=24.0",
]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-asyncio>=0.23",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "websockets>=12.0",
]
```

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes auth | `python -m pytest tests/test_auth.py -q --tb=short` | exit 0 |
| Testes completos | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Lint crítico | `ruff check bauer/auth.py --select E9,F63,F7,F82` | exit 0 |
| Verificar dependência | `pip show cryptography` | mostra versão instalada |

## Escopo

**Em escopo**:
- `bauer/auth.py` — modificar `_try_get_fernet` e `_encrypt_token`
- `pyproject.toml` — adicionar `cryptography>=41.0` a `dependencies`
- `tests/test_auth.py` — adicionar teste de erro explícito

**Fora de escopo**:
- `_xor_encrypt` e `_xor_decrypt` — mantém as funções (para descriptografar
  tokens legados existentes), mas não são mais chamadas para novos tokens
- `_decrypt_token` — já lança `ValueError` quando Fernet não está disponível
  para tokens `fernet:` prefixados; não precisa mudar
- `bauer/server.py` — não toca

## Workflow Git

- Branch: `advisor/004-fix-xor-fallback`
- Commit: `fix(auth): cryptography obrigatoria — remove fallback XOR silencioso`
- NÃO faça push nem abra PR salvo instrução explícita.

## Passos

### Passo 1: Adicionar `cryptography` às dependências core em `pyproject.toml`

No array `dependencies` de `pyproject.toml`, adicione:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "psutil>=5.9",
    "prompt-toolkit>=3.0",
    "cryptography>=41.0",   # ← adicionar aqui
]
```

Versão 41.0 é a mínima que inclui o `Fernet` e o `PBKDF2HMAC` usados em
`_derive_fernet_key`. Se quiser verificar a versão atual em uso:
`python -c "import cryptography; print(cryptography.__version__)"`.

**Verificar**: `python -c "from cryptography.fernet import Fernet"` → sem erro

### Passo 2: Modificar `_try_get_fernet` para lançar erro ao invés de retornar None

```python
# ANTES:
def _try_get_fernet(raw_key: str):
    """Retorna objeto Fernet ou None se cryptography não instalado."""
    try:
        from cryptography.fernet import Fernet
        key = _derive_fernet_key(raw_key)
        return Fernet(key)
    except ImportError:
        return None

# DEPOIS:
def _try_get_fernet(raw_key: str):
    """Retorna objeto Fernet. Lança ImportError com mensagem clara se biblioteca ausente."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ImportError(
            "A biblioteca 'cryptography' é necessária para armazenar tokens de forma segura. "
            "Instale com: pip install 'bauer-agent[keychain]' ou pip install cryptography>=41.0"
        ) from exc
    key = _derive_fernet_key(raw_key)
    return Fernet(key)
```

**Verificar**: `ruff check bauer/auth.py --select E9,F63,F7,F82` → exit 0

### Passo 3: Remover o fallback XOR de `_encrypt_token`

```python
# ANTES:
def _encrypt_token(token: str, key: str) -> str:
    """Encripta token com Fernet (AES-CBC + HMAC) ou XOR como fallback."""
    if not key or not token:
        return token
    fernet = _try_get_fernet(key)
    if fernet is not None:
        return _FERNET_PREFIX + fernet.encrypt(token.encode()).decode()
    return _xor_encrypt(token, key)

# DEPOIS:
def _encrypt_token(token: str, key: str) -> str:
    """Encripta token com Fernet (AES-CBC + HMAC). Requer biblioteca 'cryptography'."""
    if not key or not token:
        return token
    fernet = _try_get_fernet(key)  # lança ImportError se cryptography ausente
    return _FERNET_PREFIX + fernet.encrypt(token.encode()).decode()
```

**Verificar**: `ruff check bauer/auth.py --select E9,F63,F7,F82` → exit 0

### Passo 4: Confirmar que `_decrypt_token` ainda lida com XOR legado

A função `_decrypt_token` já tem lógica para descriptografar tokens legados
(sem o prefixo `fernet:`). Verifique que ela permanece intacta para migração
gradual de tokens antigos. Ela NÃO deve ser modificada neste plano.

**Verificar**: `grep -A10 "def _decrypt_token" bauer/auth.py | grep "xor"` →
deve encontrar alguma referência ao `_xor_decrypt` (suporte a tokens legados).

### Passo 5: Adicionar testes de regressão

Em `tests/test_auth.py`, adicione junto aos testes de encryption existentes:

```python
def test_encrypt_token_raises_without_cryptography(monkeypatch):
    """_encrypt_token deve lançar ImportError se cryptography não está disponível."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "cryptography.fernet":
            raise ImportError("simulando cryptography ausente")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from bauer.auth import _encrypt_token
    import importlib
    import bauer.auth
    importlib.reload(bauer.auth)  # força re-execução dos imports internos

    with pytest.raises(ImportError, match="cryptography"):
        bauer.auth._encrypt_token("sk-test-token", "minha-chave")
```

**Nota**: Se o teste acima for instável (módulos já cacheados), uma alternativa
mais simples é:

```python
def test_try_get_fernet_raises_on_import_error(monkeypatch):
    """_try_get_fernet deve propagar ImportError com mensagem clara."""
    import bauer.auth as auth_module

    with patch("bauer.auth._try_get_fernet", side_effect=ImportError("cryptography ausente")):
        with pytest.raises(ImportError, match="cryptography"):
            auth_module._encrypt_token("sk-test", "key")
```

Use a versão com `patch` se a primeira for instável.

**Verificar**: `python -m pytest tests/test_auth.py -q --tb=short` → todos passam

## Plano de testes

- 1-2 novos testes validando que `ImportError` é propagado com mensagem clara
- Os testes existentes de `_xor_encrypt` / `_xor_decrypt` podem permanecer
  (as funções ainda existem para descriptografar tokens legados)

## Critérios de conclusão

- [ ] `grep "cryptography" pyproject.toml | grep 'dependencies'` → 1 resultado (não apenas em extras)
- [ ] `grep "return _xor_encrypt" bauer/auth.py` → sem resultados (fallback removido de `_encrypt_token`)
- [ ] `grep "return None" bauer/auth.py | grep "_try_get_fernet" -A5` → sem retorno None em _try_get_fernet
- [ ] `ruff check bauer/auth.py --select E9,F63,F7,F82` → exit 0
- [ ] `python -m pytest tests/test_auth.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] `plans/README.md` linha de status atualizada

## Condições de STOP

Pare e reporte se:
- `_decrypt_token` também usa `_try_get_fernet` de forma que o erro quebre
  a descriptografia de tokens legados (verifique o fluxo completo antes de alterar).
- Há testes que simulam explicitamente `cryptography` ausente via mock e que
  passam com o fallback XOR — esses testes precisariam ser atualizados junto.
- `cryptography` já está em `dependencies` com versão diferente (não altere
  a versão existente sem entender o impacto).

## Notas de manutenção

- `_xor_encrypt` e `_xor_decrypt` podem ser removidas em uma sprint futura,
  após confirmar que nenhum usuário tem tokens legados (sem prefixo `fernet:`).
  O passo de migração seria: no próximo `bauer auth login`, re-encriptar com
  Fernet qualquer token XOR existente. Isso requer uma migração explícita,
  não é parte deste plano.
- Se no futuro for adicionado suporte a `keyring`, a chave pode ser derivada
  do keyring em vez do arquivo `.auth_key`.
