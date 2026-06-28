# Plan 001: Ocultar detalhes de exceção nas respostas HTTP 500

> **Instruções ao executor**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição de STOP ocorrer, pare e reporte — não improvise.
> Ao terminar, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 820322b..HEAD -- bauer/server.py tests/test_server_advanced.py tests/test_server_extended.py`
> Se algum arquivo em escopo mudou desde que este plano foi escrito, compare
> os trechos em "Estado atual" com o código ao vivo antes de prosseguir.
> Em caso de divergência, trate como STOP.

## Status

- **Prioridade**: P1
- **Esforço**: S
- **Risco**: LOW
- **Depende de**: nenhum
- **Categoria**: security
- **Planejado em**: commit `820322b`, 2026-06-27

## Por que isso importa

Quando uma tool call falha em `run_one_turn()`, o endpoint `/chat` e o
`/v1/chat/completions` (modo não-streaming) capturam a exceção e retornam
`str(exc)` diretamente no campo `detail` do HTTP 500. Exceções de ferramentas
tipicamente incluem paths internos do workspace, URLs de providers, mensagens
de erro do modelo, e ocasionalmente conteúdo de respostas parciais que pode
conter tokens de API. Um atacante pode disparar erros intencionalmente para
mapear a arquitetura interna do servidor.

A correção é de uma linha em cada local: substituir `detail=str(exc)` por uma
mensagem genérica e registrar a exceção completa no log do servidor.

## Estado atual

Arquivo relevante: `bauer/server.py`

**Localização 1** — endpoint `/chat` (linha ~460):

```python
# bauer/server.py:457-460
        try:
            response, tool_log = run_one_turn(ctx, router, _state["client"], _state["model"])
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
```

**Localização 2** — endpoint `/v1/chat/completions` modo não-streaming (linha ~670-673):

```python
# bauer/server.py:670-673
        try:
            response, tool_log = run_one_turn(ctx, router, _state["client"], active_model)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
```

**Convenção de logging do projeto**: o projeto usa `import logging` padrão.
O logger para o servidor é `logging.getLogger("bauer.server")` (convenção
observada em `bauer/logging_config.py` e no acesso log do middleware). Use
`_log.exception(...)` para incluir o traceback automaticamente.

O padrão do projeto para tratar erros sem expô-los ao cliente já existe em
`bauer/server.py:382-385`:
```python
        try:
            installed = client.list_models()
        except Exception:
            installed = []
```

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes | `python -m pytest tests/test_server_advanced.py tests/test_server_extended.py -q --tb=short` | exit 0 |
| Testes completos | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Lint crítico | `ruff check bauer/server.py --select E9,F63,F7,F82` | exit 0 |

## Escopo

**Em escopo** (únicos arquivos a modificar):
- `bauer/server.py`
- `tests/test_server_advanced.py` (adicionar testes)

**Fora de escopo** (não toque):
- `bauer/agent.py` — `run_one_turn` também lança exceções, mas aqui o tratamento é interno ao loop do agente, não exposto via HTTP
- Qualquer alteração na assinatura de `create_app()` ou nas classes de request/response

## Workflow Git

- Branch: `advisor/001-fix-http-exception-detail`
- Commit: `fix(server): oculta detalhes de excecao em respostas HTTP 500`
- NÃO faça push nem abra PR salvo instrução explícita.

## Passos

### Passo 1: Adicionar logger no topo de `create_app`

Em `bauer/server.py`, dentro da função `create_app()`, adicione uma linha para
criar o logger logo após a abertura da função (antes da primeira `def` interna):

```python
import logging as _logging
_log = _logging.getLogger("bauer.server")
```

Verifique se `_log` não já existe no escopo local com esse nome. Se existir com
nome diferente, adapte os passos seguintes para usar o nome existente.

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 2: Corrigir localização 1 — endpoint `/chat`

Substitua o bloco `except` em `/chat` (linhas ~457-460):

```python
# ANTES:
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

# DEPOIS:
        except Exception as exc:
            _log.exception("Erro interno em /chat: %s", exc)
            raise HTTPException(status_code=500, detail="Erro interno — consulte os logs do servidor.")
```

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 3: Corrigir localização 2 — endpoint `/v1/chat/completions` não-streaming

Substitua o bloco `except` no modo não-streaming (linhas ~670-673):

```python
# ANTES:
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

# DEPOIS:
        except Exception as exc:
            _log.exception("Erro interno em /v1/chat/completions: %s", exc)
            raise HTTPException(status_code=500, detail="Erro interno — consulte os logs do servidor.")
```

Atenção: há dois blocos `try/except` em `/v1/chat/completions` — um para
streaming (que usa `yield` e trata os erros inline) e um para não-streaming.
Modifique apenas o não-streaming. O bloco de streaming tem `except Exception as exc: tool_result = f"[Erro: {exc}]"` que é interno ao contexto (não exposto ao cliente diretamente).

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 4: Escrever testes de regressão

Em `tests/test_server_advanced.py`, adicione uma classe de teste após as classes existentes:

```python
class TestExceptionDetailHidden:
    """SEC-01: detail=str(exc) não deve vazar para clientes HTTP."""

    def test_chat_500_hides_exception_detail(self, tmp_path):
        from unittest.mock import patch, MagicMock
        app = _make_app(tmp_path)
        client = _client(app)

        with patch("bauer.server.run_one_turn", side_effect=RuntimeError("path=/home/user/.bauer/auth.json token=sk-abc123")):
            resp = client.post(
                "/chat",
                json={"message": "oi"},
                headers={"X-API-Key": ""},
            )
        assert resp.status_code == 500
        assert "path=" not in resp.json()["detail"]
        assert "token=" not in resp.json()["detail"]
        assert "sk-" not in resp.json()["detail"]
        assert "logs" in resp.json()["detail"].lower()

    def test_v1_completions_500_hides_exception_detail(self, tmp_path):
        from unittest.mock import patch
        app = _make_app(tmp_path)
        client = _client(app)

        with patch("bauer.server.run_one_turn", side_effect=ValueError("internal api key sk-secret")):
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "test-model", "messages": [{"role": "user", "content": "oi"}]},
                headers={"X-API-Key": ""},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert "sk-secret" not in str(body)
```

**Verificar**: `python -m pytest tests/test_server_advanced.py::TestExceptionDetailHidden -q --tb=short` → 2 passed

## Plano de testes

- 2 novos testes na classe `TestExceptionDetailHidden` em `tests/test_server_advanced.py`
- Padrão: siga a estrutura de `_make_app()` + `_client()` já existente no arquivo
- Cada teste confirma que o corpo da resposta 500 não contém o conteúdo da exceção

## Critérios de conclusão

- [ ] `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0
- [ ] `python -m pytest tests/test_server_advanced.py -q --tb=short` → todos passam, incluindo 2 novos
- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] `grep -n "detail=str(exc)" bauer/server.py` → sem resultados
- [ ] Nenhum arquivo fora do escopo foi modificado (`git status`)
- [ ] `plans/README.md` linha de status atualizada

## Condições de STOP

Pare e reporte se:
- O código em `bauer/server.py:457-460` não corresponde ao trecho em "Estado atual" (drift).
- Há um terceiro `detail=str(exc)` em `server.py` além dos dois documentados.
- `_log` já existe como variável local em `create_app()` com semântica diferente.
- O passo de verificação falha duas vezes após tentativa razoável de correção.

## Notas de manutenção

- Se no futuro for adicionado middleware global de tratamento de exceções (FastAPI exception handlers), esse padrão local pode ser consolidado lá — os dois `except` locais podem ser removidos em favor de um `@app.exception_handler(Exception)`.
- Revisor: confirme que `_log.exception()` (não `_log.error()`) é usado — `exception()` inclui o traceback completo, que é essencial para debug de produção.
