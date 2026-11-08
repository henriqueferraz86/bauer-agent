# BACKLOG — Sprint 63

## Tarefas

### 1. Reproduzir WEB-08 e WEB-10 no código atual

Status: concluído

- WEB-08 em Chromium real (`browser_logout_storage.py`): storage retido e a
  segunda aba ainda mostrando a conversa depois do logout.
- WEB-10 com `receive` contado (`bauer_web_body_limit_repro.py`): 202/202 e
  302/302 chunks consumidos antes da rejeição.

### 2. Política de retenção local e limpeza no logout (WEB-08)

Status: concluído

- `localSession.ts`: `clearLocalSession`, `onLocalSessionCleared`,
  `listenForRemoteLogout` e `logoutAndClear`.
- `AuthContext`: logout explícito, expiração (401 autenticado) e logout de
  outra aba.

### 3. Turno de chat não regrava depois do logout

Status: concluído

- `chatStream.resetChatTurn` + geração de turno.

### 4. Limite de body antes do parser (WEB-10)

Status: concluído

- `BodySizeLimitMiddleware` mais externo, com limites por rota:
  `/transcribe`, `/v1/*` e o padrão.

### 5. Regressões e bundle

Status: concluído

- `tests/test_server_body_limit_asgi.py` (12 testes): multipart chunked,
  `Content-Length` grande, mentiroso e inválido, JSON chunked, `/v1` com teto
  próprio, request válido, TestClient real, GET, app que engole a exceção e
  limites por rota.
- `desktop/src/localSession.test.ts` (6 testes): storage `bauer.*` nos dois
  storages, falha da revogação, sinal entre abas, aba receptora, storage
  bloqueado e turno SSE em andamento.
- `npm run build` com o bundle versionado em `bauer/static/` (só
  `index-*.js` e `index.html` mudaram).

## Notas de entrega

- Branch `codex/sprint-63-security-web-lifecycle`, empilhada sobre
  `codex/sprint-60-security-kernel-gates`.
- Prova pós-correção:
  - WEB-08 (Chromium): `depois_logout_chaves: []`,
    `outra_aba_mostra_login: true`, `outra_aba_texto_visivel: false`,
    `relogin_reidrata_conversa: false`.
  - WEB-10: `chunks_consumed` 6/202 (multipart) e 9/302 (JSON), ambos 413.
- Falha sem a correção, conferida: os 7 testes comportamentais de WEB-10 sem o
  `add_middleware`, e o teste de turno em andamento com o `chatStream.ts` de
  `HEAD`. Os demais testes do vitest importam o módulo novo e,
  trivialmente, também falham sem ele.
- `npm audit --audit-level=high`: exit 0. Há 11 avisos *moderate* já
  existentes (vitest/mocker, glob, minimatch na cadeia de fontes), fora de
  escopo e sem mudança de dependência.
- Fica em aberto, por decisão registrada no SPEC: autenticação antecipada de
  upload e abort do SSE no logout (contrato WEB-09).

## Gates

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
cd desktop && npm ci && npm run build && npm test && npm audit --audit-level=high
```
