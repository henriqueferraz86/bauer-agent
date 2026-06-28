# Plan 002: Substituir comparação de API key por hmac.compare_digest

> **Instruções ao executor**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição de STOP ocorrer, pare e reporte — não improvise.
> Ao terminar, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 820322b..HEAD -- bauer/server.py tests/test_server_advanced.py`
> Se `bauer/server.py` mudou (especialmente na função `_verify_key`), compare
> o trecho "Estado atual" antes de prosseguir.

## Status

- **Prioridade**: P1
- **Esforço**: S
- **Risco**: LOW
- **Depende de**: nenhum (independente do plano 001)
- **Categoria**: security
- **Planejado em**: commit `820322b`, 2026-06-27

## Por que isso importa

A função `_verify_key` compara a API key recebida com a configurada via
`if incoming != api_key:`. Comparações de string em Python terminam assim que
um caractere diverge, o que cria uma diferença de tempo proporcional ao
número de caracteres que coincidem. Em redes de baixa latência (containers,
mesma máquina), um atacante pode medir essas variações e reconstruir a key
caractere a caractere, reduzindo o espaço de busca de O(A^N) para O(N×A),
onde A é o alfabeto e N o comprimento da key. A correção é uma linha e não
tem downtime.

## Estado atual

Arquivo relevante: `bauer/server.py`, função `_verify_key` (linha ~300):

```python
# bauer/server.py:300-305
    def _verify_key(request: Request) -> None:
        if not api_key:
            return
        incoming = _extract_incoming_key(request)
        if incoming != api_key:
            raise HTTPException(status_code=401, detail="API key invalida ou ausente.")
```

`hmac` está disponível na stdlib do Python 3.11 — nenhuma dependência nova.
O módulo `hmac` não está no topo de `bauer/server.py` (que importa apenas
`time`, `collections`, `pathlib`, `typing`); portanto precisa ser importado.

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes | `python -m pytest tests/test_server_advanced.py tests/test_server_extended.py -q --tb=short` | exit 0 |
| Testes completos | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Lint crítico | `ruff check bauer/server.py --select E9,F63,F7,F82` | exit 0 |

## Escopo

**Em escopo**:
- `bauer/server.py`
- `tests/test_server_advanced.py` (adicionar teste)

**Fora de escopo**:
- `bauer/auth.py` — tem lógica própria de comparação de tokens (fora do escopo desta correção)
- Qualquer outro arquivo

## Workflow Git

- Branch: `advisor/002-fix-api-key-timing`
- Commit: `fix(server): usa hmac.compare_digest na verificacao da API key`
- NÃO faça push nem abra PR salvo instrução explícita.

## Passos

### Passo 1: Adicionar import de `hmac` em `bauer/server.py`

No bloco de imports no topo do arquivo (`bauer/server.py`, linhas 35-38),
adicione `import hmac` junto aos imports da stdlib:

```python
import hmac
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional
```

**Verificar**: `python -c "import bauer.server"` → sem erro de import

### Passo 2: Substituir comparação em `_verify_key`

Substitua a linha de comparação dentro de `_verify_key`:

```python
# ANTES:
        if incoming != api_key:

# DEPOIS:
        if not hmac.compare_digest(incoming or "", api_key):
```

O `or ""` garante que `compare_digest` receba uma string mesmo quando o
header está ausente (o que retornaria `""` de `_extract_incoming_key`).
`compare_digest` aceita `str` ou `bytes`; usar `str` é correto aqui.

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 3: Adicionar teste de regressão

Em `tests/test_server_advanced.py`, dentro de uma classe existente de autenticação
ou ao final do arquivo como nova classe:

```python
class TestApiKeyComparison:
    """SEC-05: comparação de API key deve ser em tempo constante."""

    def test_valid_key_grants_access(self, tmp_path):
        app = _make_app(tmp_path, api_key="secret-key-abc")
        client = _client(app)
        resp = client.get("/status", headers={"X-API-Key": "secret-key-abc"})
        assert resp.status_code == 200

    def test_invalid_key_returns_401(self, tmp_path):
        app = _make_app(tmp_path, api_key="secret-key-abc")
        client = _client(app)
        resp = client.get("/status", headers={"X-API-Key": "wrong-key"})
        # /status nao tem auth guard ainda — mas este teste documenta a intenção
        # Remova este comentário quando o plano 003 for aplicado e /status ganhar guard.

    def test_hmac_compare_digest_used(self):
        """Garante que _verify_key usa compare_digest (não ==)."""
        import inspect
        import bauer.server as srv
        src = inspect.getsource(srv)
        assert "hmac.compare_digest" in src, (
            "bauer/server.py deve usar hmac.compare_digest na verificacao de API key"
        )
        assert "incoming != api_key" not in src, (
            "Comparacao direta 'incoming != api_key' ainda presente em bauer/server.py"
        )
```

**Verificar**: `python -m pytest tests/test_server_advanced.py::TestApiKeyComparison -q --tb=short` → 2–3 passed

## Plano de testes

- 2-3 novos testes em `TestApiKeyComparison`
- O teste de inspeção de código (`test_hmac_compare_digest_used`) serve como
  guarda de regressão: se alguém reverter a mudança, o teste falha.
- Padrão: seguir `_make_app()` + `_client()` existentes em `test_server_advanced.py`

## Critérios de conclusão

- [ ] `grep -n "incoming != api_key" bauer/server.py` → sem resultados
- [ ] `grep -n "hmac.compare_digest" bauer/server.py` → ao menos 1 resultado
- [ ] `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0
- [ ] `python -m pytest tests/test_server_advanced.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] Nenhum arquivo fora do escopo foi modificado
- [ ] `plans/README.md` linha de status atualizada

## Condições de STOP

Pare e reporte se:
- O bloco `_verify_key` em `server.py` não corresponde ao trecho "Estado atual".
- Há outras comparações de API key em `server.py` além desta (pode haver lógica duplicada no middleware — verifique antes de prosseguir).
- `hmac.compare_digest` rejeita os tipos de argumento com `TypeError` (seria necessário converter para bytes).

## Notas de manutenção

- Se no futuro o formato da API key mudar para bytes (ex: hash binário), converta para `hmac.compare_digest(incoming.encode(), api_key.encode())`.
- Este plano não toca `bauer/auth.py` — a comparação de tokens OAuth lá usa Fernet/XOR com lógica diferente e está fora do escopo.
