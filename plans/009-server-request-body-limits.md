# Plan 009: Limitar tamanho do body nos endpoints do `bauer serve` (DoS por payload gigante)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report. When done, update this
> plan's status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/server.py`
> If `bauer/server.py` changed, compare "Current state" excerpts against live
> code before proceeding; on mismatch, treat as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

Os modelos Pydantic das requisições do `bauer serve` (`ChatRequest.message` e
`OAICompletionRequest.messages`) não têm limite de tamanho. O FastAPI
desserializa o body inteiro antes de qualquer lógica rodar, então um cliente
pode enviar uma única requisição com uma string de gigabytes e exaurir a
memória do processo, derrubando o servidor (DoS) ou impedindo requisições
legítimas. O fix é declarativo e de baixo risco: adicionar constraints
`max_length`/`max_items` nos campos Pydantic — a validação rejeita o excesso
com um 422 antes de materializar o payload monstro.

## Current state

- `bauer/server.py` — API HTTP (FastAPI, OpenAI-compat). Schemas relevantes:

```python
# bauer/server.py:197-199
    class ChatRequest(PydanticModel):
        message: str
        session_id: Optional[str] = None
```

```python
# bauer/server.py:652-662
    class OAIMessage(PydanticModel):
        role: str
        content: str

    class OAICompletionRequest(PydanticModel):
        model: Optional[str] = None
        messages: list[OAIMessage]
        stream: bool = False
        session_id: Optional[str] = None    # campo body (ignorado em favor do header)
        max_tokens: Optional[int] = None
        temperature: Optional[float] = None
```

- `PydanticModel` é o `BaseModel` do Pydantic v2 (confirme o alias no topo de
  `server.py`: `grep -n "PydanticModel\|import BaseModel\|from pydantic" bauer/server.py`).
- Endpoints que consomem esses schemas: `/chat` (linha 463, `chat(req: ChatRequest, ...)`)
  e `/v1/chat/completions` (linha 664, `oai_chat_completions(...)` iterando
  `req.messages` na linha 689).
- Há também um endpoint GET `/stream` (linha 512) que recebe `message` como
  `Query(...)` — querystrings já são limitadas pelo servidor HTTP; fora do
  escopo deste plano.

### Convenções do repo a seguir
- Pydantic v2. Use `Field(..., max_length=N)` para strings e
  `Field(..., max_items=N)` / `Field(..., min_length=1, max_length=N)` para
  listas (em Pydantic v2, listas usam `max_length` para o número de itens).
- Importe `Field` do pydantic no topo de `server.py` se ainda não estiver
  importado (`grep -n "Field" bauer/server.py`).

## Commands you will need

| Purpose   | Command                                                     | Expected |
|-----------|-------------------------------------------------------------|----------|
| Testes    | `.venv/Scripts/python.exe -m pytest tests/ -k "server" -q`  | all pass |
| Import    | `.venv/Scripts/python.exe -c "import bauer.server"`         | exit 0   |

## Scope

**In scope**:
- `bauer/server.py` (só os campos dos 2 schemas + import de `Field` se preciso)
- `tests/test_server_body_limits.py` (criar)

**Out of scope** (NÃO tocar):
- A lógica dos endpoints (`chat`, `oai_chat_completions`) — só os schemas.
- O endpoint `/stream` (GET com Query) — querystring já tem limite do servidor.
- `/transcribe` (upload de arquivo) — upload tem tratamento próprio; fora daqui.
- Qualquer mudança no shape de resposta — clientes dependem dela.

## Git workflow

- Branch: `advisor/009-server-request-body-limits`
- Commit style: conventional commits. Ex.:
  `fix(security): limita tamanho do body em /chat e /v1/chat/completions (anti-DoS)`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Adicionar limites nos schemas

Em `bauer/server.py`, aplique constraints. Valores sugeridos (generosos o
suficiente para uso real, mas que barram payloads absurdos):
- `ChatRequest.message`: `max_length=100_000` (~100 KB de texto).
- `OAIMessage.content`: `max_length=200_000`.
- `OAICompletionRequest.messages`: no máximo `max_length=200` itens (lista).

Forma-alvo:

```python
    class ChatRequest(PydanticModel):
        message: str = Field(..., max_length=100_000)
        session_id: Optional[str] = None
```

```python
    class OAIMessage(PydanticModel):
        role: str
        content: str = Field(..., max_length=200_000)

    class OAICompletionRequest(PydanticModel):
        model: Optional[str] = None
        messages: list[OAIMessage] = Field(..., min_length=1, max_length=200)
        stream: bool = False
        session_id: Optional[str] = None
        max_tokens: Optional[int] = None
        temperature: Optional[float] = None
```

Garanta que `Field` está importado (`from pydantic import BaseModel, Field` ou
equivalente ao que já existe).

**Verify**: `.venv/Scripts/python.exe -c "import bauer.server"` → exit 0.

### Step 2: Testes

Crie `tests/test_server_body_limits.py` (ver Test plan).

**Verify**: `.venv/Scripts/python.exe -m pytest tests/test_server_body_limits.py -q` → all pass.

## Test plan

- Novo arquivo `tests/test_server_body_limits.py`. Se já houver testes que
  usam `fastapi.testclient.TestClient` sobre o app (`grep -rln "TestClient" tests/`),
  modele por eles. Se preferir testar só a validação do schema (sem subir o
  app), instancie o modelo Pydantic diretamente e verifique que ele levanta
  `pydantic.ValidationError` — é mais simples e hermético.
- Casos (via instanciação direta do schema, sem rede):
  1. `ChatRequest(message="x" * 100_001)` levanta `ValidationError`.
  2. `ChatRequest(message="ok")` é válido.
  3. `OAICompletionRequest(messages=[])` levanta `ValidationError` (min_length=1).
  4. `OAICompletionRequest(messages=[OAIMessage(role="user", content="oi")])`
     é válido.
  5. `OAICompletionRequest(messages=[OAIMessage(role="user", content="oi")] * 201)`
     levanta `ValidationError` (max_length=200 itens).
- **Nota**: os schemas são definidos DENTRO da função factory do app em
  `server.py` (classes aninhadas). Se não forem importáveis top-level, exponha-os
  via a instância do app com `TestClient` enviando JSON e assertando status 422.
  Prefira o caminho que funcionar; documente qual usou no docstring do teste.
- Verificação: `.venv/Scripts/python.exe -m pytest tests/test_server_body_limits.py -q`
  → all pass (5 casos).

## Done criteria

TODAS devem valer:

- [ ] `.venv/Scripts/python.exe -c "import bauer.server"` sai 0
- [ ] `.venv/Scripts/python.exe -m pytest tests/test_server_body_limits.py -q` passa
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -k server -q` continua passando
- [ ] `grep -n "max_length" bauer/server.py` retorna ≥2 ocorrências novas
- [ ] Nenhum arquivo fora do in-scope modificado (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte se:

- Os excerpts dos schemas não baterem com o código atual (drift).
- Os schemas estiverem definidos de forma que `Field` já não seja importável
  no escopo (ex.: metaprogramação) — reporte em vez de reestruturar.
- Algum teste existente de server quebrar por assumir mensagens > 100 KB
  (improvável, mas se acontecer, reporte — talvez o limite precise subir).
- Descobrir que existe um terceiro endpoint POST consumindo body de tamanho
  ilimitado não listado aqui — reporte para ampliar o escopo conscientemente.

## Maintenance notes

- Se um caso de uso legítimo precisar de mensagens maiores que os limites,
  suba os valores — eles são um teto de sanidade, não um limite de negócio.
- Para defesa em profundidade, um limite global de body no servidor
  (uvicorn/reverse proxy) complementa isto; fora do escopo deste plano.
- O reviewer deve conferir que os limites não são pequenos demais para os
  prompts reais que o projeto usa (100 KB de mensagem já é bastante texto).
