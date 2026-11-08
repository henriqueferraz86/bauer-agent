# SPEC — Sprint 63: lifecycle web — logout/storage e limite de body antes do parser

Plano: [`plans/064-seguranca-auditoria-2026-10-12.md`](../../plans/064-seguranca-auditoria-2026-10-12.md),
lote 8 de [`CORRECTIONS.md`](../../docs/audits/2026-10-12/CORRECTIONS.md)
(WEB-08, WEB-10). Evidência original em
[`WEB.md`](../../docs/audits/2026-10-12/WEB.md) e
[`REPORT.md`](../../docs/audits/2026-10-12/REPORT.md). Base: sprint 60
(`codex/sprint-60-security-kernel-gates`), empilhada como manda o plano.

## Reprodução no código atual (primeiro passo)

O plano registrava os dois achados como "não confirmados em 2026-11-08",
porque os scripts antigos quebravam antes. Os dois foram reproduzidos de novo,
sem rede e sem provider real.

**WEB-08: navegador real.** Usei o Chromium do cache do Playwright, o app real
do `bauer serve` com web auth ligada e um client falso
(`docs/audits/2026-10-12/repro/browser_logout_storage.py`). A sequência foi:
cadastro, uma mensagem, segunda aba, Sair. Antes da correção:

```text
{"antes_logout_chaves": ["bauer.chatState.v1"], "depois_logout_chaves": ["bauer.chatState.v1"],
 "chat_state_retido": "{\"messages\":[{\"role\":\"user\",\"text\":\"mensagem sensível WEB08\"…}],\"sessionId\":\"081c628f\"…}",
 "sessions_status": 401, "outra_aba_mostra_login": false, "outra_aba_texto_visivel": true, "WEB08_reproduz": true}
```

O cookie foi revogado (`/sessions` → 401), mas a conversa e o `session_id`
continuaram em `localStorage`. A segunda aba seguia mostrando o texto.

**WEB-10: `receive` ASGI contado.** Usei limite pequeno, corpo sem
`Content-Length` e chunks de 1 KiB
(`docs/audits/2026-10-12/repro/bauer_web_body_limit_repro.py`). Antes da
correção:

```text
{"case": "multipart_chunked_transcribe", "chunks_total": 202, "chunks_consumed": 202, "status": [413]}
{"case": "json_chunked_chat", "chunks_total": 302, "chunks_consumed": 302, "status": [422]}
```

O `/transcribe` só rejeitou depois de o parser multipart receber o upload
inteiro, com `MAX_AUDIO_BYTES=4 KiB` e 200 KiB recebidos. O `/chat`
materializou 300 KiB antes do 422 do Pydantic.

## Problema

- **WEB-08.** `AuthContext.logout` só chamava `POST /auth/logout` e
  `refresh()`. `bauer.chatState.v1` (transcript, `session_id`, contexto do
  projeto) ficava fora da proteção HttpOnly e reidratava no próximo login. O
  turno de chat em memória (`chatStream.active`) e as outras abas também não
  eram encerrados.
- **WEB-10.** Não existia limite de bytes antes do parser. O único limite de
  upload ficava dentro do endpoint, e os limites de JSON eram só do Pydantic.
  Um `Content-Length` ausente (chunked) ou mentiroso não era barrado.

## Objetivo

- Logout limpa o storage do navegador.
- O limite de body é aplicado antes do parser, incluindo multipart e chunked.

## Escopo

- `desktop/src/localSession.ts`: política de retenção local, limpeza de
  `bauer.*` em `localStorage` e `sessionStorage`, reset do estado em memória,
  sinal entre abas e `logoutAndClear`.
- `desktop/src/chatStream.ts`: `resetChatTurn` e geração de turno, para que um
  SSE em andamento não regrave a conversa depois do logout.
- `desktop/src/auth/AuthContext.tsx`: logout explícito, expiração (401 depois
  de autenticado) e logout vindo de outra aba.
- `bauer/server_body_limit.py`: middleware ASGI puro e mais externo, com limite
  por categoria de rota. `bauer/server.py` instala o middleware no fim de
  `create_app`.
- Bundle `bauer/static/` reconstruído, já que a fonte do SPA mudou. Esse é o
  precedente do repo: commits de `desktop/src` levam o bundle junto.

## Decisões

1. **Política de retenção (WEB-08).** O navegador não guarda a conversa além
   da sessão autenticada. O servidor continua dono do histórico
   (single-admin), e o que some é só a cópia local. Os gatilhos são:
   - logout explícito: limpa mesmo se `POST /auth/logout` falhar, porque quem
     clicou em Sair quer que este navegador pare de mostrar a conversa. O erro
     ainda chega ao caller;
   - expiração: um 401 recebido quando a UI estava autenticada com web auth
     ligada. Em servidor sem web auth (modo API key/aberto), um 401 não apaga
     nada, porque não há sessão de navegador a encerrar;
   - logout em outra aba: o sinal vem pela chave `bauer.logout.signal` (evento
     `storage`, mesma origem). A aba que recebe zera a memória e volta ao
     login.
2. **Escopo da limpeza.** Toda chave com prefixo `bauer.` nos dois storages, e
   não só `bauer.chatState.v1`. Assim, uma chave nova da UI não escapa da
   regra por esquecimento. Chaves de outras origens ou apps não são tocadas.
3. **Turno em andamento.** O SSE não é abortado: abortar com retomada é o
   WEB-09, contrato da sprint 27. O turno antigo perde o direito de publicar e
   persistir (contador de geração). A resposta termina no servidor e fica no
   histórico dele, não no navegador deslogado.
4. **Navegador real.** O Chromium do cache Playwright estava disponível, então
   a prova do WEB-08 é dinâmica (antes e depois) em navegador real. A
   regressão permanente roda no vitest (`desktop/src/localSession.test.ts`),
   sem depender de navegador no CI.
5. **Limites por rota (WEB-10).** Um teto único reprovaria requests válidos.
   - `/transcribe`: `MAX_AUDIO_BYTES` (25 MB) + 1 MiB de envelope multipart.
   - `/v1/*`: 48 MiB. O schema aceita 200 mensagens de 200 mil caracteres
     (cerca de 40 MB em ASCII), e o teto acompanha esse contrato em vez de
     reduzi-lo.
   - demais rotas: 4 MiB, bem acima do maior `/chat` válido (100 mil
     caracteres, cerca de 600 KB com escapes) e dos formulários da UI.
   Os valores são constantes do módulo, e não campo de config: as seções de
   config são `extra="forbid"`, e um campo novo quebraria configs
   distribuídas.
6. **Como o corte funciona.** Com `Content-Length` acima do limite, o 413 sai
   sem ler um byte. Com `Content-Length` malformado, sai 400. Sem
   `Content-Length`, ou com um valor mentiroso, o `receive` contado
   interrompe o app no primeiro chunk que passa do limite. A resposta que o
   app tentar mandar depois disso, seja o 500 do `ServerErrorMiddleware` ou
   um 200 de um endpoint que engoliu a exceção, é descartada, e o cliente
   recebe 413. A decisão vem de um flag, e não do tipo da exceção, que pode
   chegar embrulhada pelo `BaseHTTPMiddleware`.
7. **Só métodos com corpo.** POST, PUT, PATCH e DELETE passam pelo middleware.
   GET, SSE (`/stream`) e websocket passam direto.
8. **Autenticação antecipada de upload** (sugestão do `WEB.md`) fica fora de
   escopo. Com o corte antes do parser, um cliente não autenticado consome no
   máximo o limite da rota, e não mais o upload inteiro.

## Fora de escopo

- WEB-09 (abort/continuidade do SSE ao sair da tela) e UI-01 (HUD), entregues
  na sprint 27.
- Criptografia/opt-in de storage por conta.
- Achados de outros tracks (WEB-01/02/03, T01–T10, CI Windows).

## Critérios de aceite

- [x] Logout real no navegador: `localStorage` sem chaves `bauer.*`. A segunda
      aba volta ao login sem mostrar o texto, e um novo login não reidrata a
      conversa.
- [x] Um turno SSE em andamento não regrava a conversa depois do logout.
- [x] Multipart chunked para `/transcribe`: no máximo 7 de 202 chunks
      consumidos, 413, e `transcribe_audio` não é chamado.
- [x] JSON chunked para `/chat`: no máximo 10 de 302 chunks, 413, e o turno
      não roda.
- [x] `Content-Length` grande → 413 com 0 chunks lidos. Mentiroso → corte no
      limite. Inválido → 400.
- [x] Request dentro do limite e o `/v1` com payload maior que o padrão
      continuam aceitos.
- [x] As regressões falham sem a correção: as 7 de WEB-10 falham sem o
      `add_middleware`, e a de turno em andamento falha com o `chatStream.ts`
      anterior.

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
cd desktop && npm ci && npm run build && npm test && npm audit --audit-level=high
uv run python docs/audits/2026-10-12/repro/bauer_web_body_limit_repro.py
uv run --with playwright==1.63.0 python docs/audits/2026-10-12/repro/browser_logout_storage.py
```
