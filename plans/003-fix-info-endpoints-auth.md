# Plan 003: Adicionar guarda de autenticação nos endpoints informativos

> **Instruções ao executor**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição de STOP ocorrer, pare e reporte — não improvise.
> Ao terminar, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 820322b..HEAD -- bauer/server.py tests/test_server_advanced.py tests/test_server_extended.py`
> Compare os trechos "Estado atual" com o código ao vivo antes de prosseguir.

## Status

- **Prioridade**: P1
- **Esforço**: S
- **Risco**: LOW
- **Depende de**: nenhum (pode aplicar junto com 001 e 002)
- **Categoria**: security
- **Planejado em**: commit `820322b`, 2026-06-27

## Por que isso importa

Quando `api_key` está configurado, os endpoints `/status`, `/tools`,
`/metrics` e `/models` respondem sem verificar a key. Isso significa que
qualquer pessoa que souber a URL do servidor (mesmo sem a key) pode:

- `/status` — descobrir o modelo ativo, provider, lista completa de tools, e se auth está ativo
- `/tools` — enumerar todas as ferramentas disponíveis (inclui `run_command`, `read_file`, etc.)
- `/metrics` — obter contagens de requisições, erros e rate-limit violations
- `/models` — listar todos os modelos Ollama instalados na máquina

Essa informação facilita reconhecimento para ataques direcionados.

**`/health` permanece sem guard** — é necessário para health checks de load
balancers e orquestradores de container (Docker, Kubernetes) que precisam
verificar o servidor sem credenciais. Esta é uma convenção padrão.

## Estado atual

Arquivo relevante: `bauer/server.py`

Endpoints **sem** `Depends(_verify_key)` que deveriam ter:

```python
# bauer/server.py:355-389 (trechos relevantes)

@app.get("/health")          # ← mantém SEM guard (load balancer liveness)
def health():
    return {"status": "ok", "model": _state["model"]}

@app.get("/status")          # ← ADICIONAR guard
def status():
    ...

@app.get("/metrics", include_in_schema=False)   # ← ADICIONAR guard
def metrics():
    ...

@app.get("/tools")           # ← ADICIONAR guard
def tools_list():
    ...

@app.get("/models")          # ← ADICIONAR guard
def models_list():
    ...
```

Endpoint que **já tem** o guard (use como referência de sintaxe):

```python
# bauer/server.py:392
@app.post("/models/switch")
def models_switch(body: dict, _: None = Depends(_verify_key)):
```

A função `_verify_key` retorna `None` quando `api_key` está vazio, portanto
adicionar o `Depends` não quebra instalações sem autenticação.

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes | `python -m pytest tests/test_server_advanced.py tests/test_server_extended.py -q --tb=short` | exit 0 |
| Testes completos | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Lint crítico | `ruff check bauer/server.py --select E9,F63,F7,F82` | exit 0 |

## Escopo

**Em escopo**:
- `bauer/server.py` — apenas adicionar `_: None = Depends(_verify_key)` em 4 funções
- `tests/test_server_advanced.py` — adicionar testes de auth nos 4 endpoints

**Fora de escopo**:
- `/health` — NÃO adicionar guard aqui (liveness check público é intencional)
- `/v1/models` — já tem `Depends(_verify_key)` (linha ~702)
- Qualquer outro arquivo

## Workflow Git

- Branch: `advisor/003-fix-info-endpoints-auth`
- Commit: `fix(server): adiciona auth guard em /status /tools /metrics /models`
- NÃO faça push nem abra PR salvo instrução explícita.

## Passos

### Passo 1: Adicionar `Depends` à função `status`

```python
# ANTES:
@app.get("/status")
def status():

# DEPOIS:
@app.get("/status")
def status(_: None = Depends(_verify_key)):
```

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 2: Adicionar `Depends` à função `metrics`

```python
# ANTES:
@app.get("/metrics", include_in_schema=False)
def metrics():

# DEPOIS:
@app.get("/metrics", include_in_schema=False)
def metrics(_: None = Depends(_verify_key)):
```

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 3: Adicionar `Depends` à função `tools_list`

```python
# ANTES:
@app.get("/tools")
def tools_list():

# DEPOIS:
@app.get("/tools")
def tools_list(_: None = Depends(_verify_key)):
```

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 4: Adicionar `Depends` à função `models_list`

```python
# ANTES:
@app.get("/models")
def models_list():

# DEPOIS:
@app.get("/models")
def models_list(_: None = Depends(_verify_key)):
```

**Verificar**: `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0

### Passo 5: Confirmar que `/health` não foi alterado

```python
# DEVE permanecer exatamente assim (SEM Depends):
@app.get("/health")
def health():
    return {"status": "ok", "model": _state["model"]}
```

**Verificar**: `grep -A2 'app.get("/health")' bauer/server.py` → não deve conter `Depends`

### Passo 6: Escrever testes de regressão

Em `tests/test_server_advanced.py`, adicione a classe:

```python
class TestInfoEndpointsAuth:
    """SEC-03: endpoints informativos exigem auth quando api_key está configurada."""

    PROTECTED = ["/status", "/tools", "/metrics", "/models"]

    def test_info_endpoints_require_auth_when_key_configured(self, tmp_path):
        app = _make_app(tmp_path, api_key="test-secret")
        client = _client(app)
        for path in self.PROTECTED:
            resp = client.get(path)
            assert resp.status_code == 401, (
                f"{path} deve retornar 401 sem API key quando auth está ativo"
            )

    def test_info_endpoints_accept_valid_key(self, tmp_path):
        app = _make_app(tmp_path, api_key="test-secret")
        client = _client(app)
        for path in self.PROTECTED:
            resp = client.get(path, headers={"X-API-Key": "test-secret"})
            assert resp.status_code == 200, (
                f"{path} deve aceitar key válida"
            )

    def test_health_remains_public(self, tmp_path):
        """GET /health deve responder sem autenticação mesmo com api_key configurada."""
        app = _make_app(tmp_path, api_key="test-secret")
        client = _client(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_info_endpoints_accessible_without_auth_config(self, tmp_path):
        """Sem api_key configurada, todos os endpoints devem responder livremente."""
        app = _make_app(tmp_path)  # api_key="" por padrão
        client = _client(app)
        for path in self.PROTECTED + ["/health"]:
            resp = client.get(path)
            assert resp.status_code == 200, (
                f"{path} deve ser acessível sem api_key configurada"
            )
```

**Verificar**: `python -m pytest tests/test_server_advanced.py::TestInfoEndpointsAuth -q --tb=short` → 4 passed

## Plano de testes

- 4 novos testes em `TestInfoEndpointsAuth`
- O teste `test_health_remains_public` é o guarda de regressão crítico — garante que `/health` nunca ganhe auth inadvertidamente

## Critérios de conclusão

- [ ] `grep -B1 "def status(" bauer/server.py | grep "Depends(_verify_key)"` → 1 resultado
- [ ] `grep -B1 "def metrics(" bauer/server.py | grep "Depends(_verify_key)"` → 1 resultado
- [ ] `grep -B1 "def tools_list(" bauer/server.py | grep "Depends(_verify_key)"` → 1 resultado
- [ ] `grep -B1 "def models_list(" bauer/server.py | grep "Depends(_verify_key)"` → 1 resultado
- [ ] `grep -A3 'app.get("/health")' bauer/server.py` → NÃO contém `Depends`
- [ ] `ruff check bauer/server.py --select E9,F63,F7,F82` → exit 0
- [ ] `python -m pytest tests/test_server_advanced.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] `plans/README.md` linha de status atualizada

## Condições de STOP

Pare e reporte se:
- O código de `health()`, `status()`, `metrics()`, `tools_list()`, `models_list()` não corresponde aos trechos "Estado atual" (drift).
- Descobrir um quinto endpoint informativo sem guard além dos 4 listados.
- Algum teste existente falha após adicionar o `Depends` (pode indicar que um teste chama esses endpoints sem passar key).

## Notas de manutenção

- Ao adicionar um novo endpoint informativo no futuro, use `_: None = Depends(_verify_key)` por padrão e documente explicitamente na docstring do endpoint se ele for intencionalmente público.
- O docstring no topo de `server.py` que lista endpoints com `[auth]` deve ser atualizado para incluir `/status`, `/tools`, `/metrics`, `/models`.
