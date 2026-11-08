# ARCHITECTURE — Sprint 63

## WEB-08: retenção local da SPA

```text
Config "Sair" ── AuthContext.logout ── logoutAndClear(POST /auth/logout)
                                            └─ finally clearLocalSession()
401 (AUTH_REQUIRED_EVENT) ── se enabled && authenticated ── clearLocalSession()
outra aba: evento storage key=bauer.logout.signal ── clearLocalSession({broadcast:false})
                                                    └─ setState(deslogado) + refresh()

clearLocalSession()
  ├─ remove toda chave "bauer.*" de localStorage e sessionStorage (try/catch)
  ├─ roda os resetters registrados por onLocalSessionCleared
  │     └─ chatStream: resetChatTurn() (generation++, active=null)
  │                    chatActivityStore.selectSession("", true)
  └─ broadcast: setItem + removeItem de bauer.logout.signal
```

`runChatTurn` captura `generation` no início. Os callbacks do SSE, `publish`,
`updateLast` e o `finally` não fazem nada quando a geração mudou. Por isso o
`persist` (que grava `bauer.chatState.v1`) nunca roda depois do logout. O
`AuthGate` desmonta o `App` quando a sessão cai, e o novo login monta o
`Chat` com `loadChatState()` vazio.

`localSession.ts` não importa nada da UI, e `chatStream.ts` se registra nele.
Assim não há ciclo de import.

## WEB-10: `BodySizeLimitMiddleware`

```text
uvicorn ── ServerErrorMiddleware ── BodySizeLimitMiddleware ── _metrics (BaseHTTP) ── CORS/GZip ── rotas
                                     │
                                     ├─ método sem corpo / websocket → passa direto
                                     ├─ Content-Length inválido → 400
                                     ├─ Content-Length > limite_para(path) → 413 (0 bytes lidos)
                                     └─ receive_contado: soma len(body); > limite →
                                          excedido=True, raise _CorpoExcedido
                                        send_guardado: descarta mensagens após excedido
                                        except BaseException: re-levanta só se não excedido
                                        excedido e resposta não iniciada → 413
```

O `app.add_middleware` fica no fim de `create_app`. No Starlette, o último
middleware adicionado é o mais externo dos middlewares do usuário, então o
corte acontece antes de `_metrics_middleware`, CORS, GZip, dependências e
parsers. Os limites são lidos das constantes do módulo a cada request
(`limite_para`), o que permite monkeypatch nos testes. O limite de áudio vem
de `bauer.transcription.MAX_AUDIO_BYTES`, a mesma fonte do endpoint.

O check que já existia dentro de `/transcribe` foi mantido: ele dá a mensagem
específica de áudio e a validação de extensão antes de gravar em disco.

## Arquivos

- `bauer/server_body_limit.py` (novo), `bauer/server.py` (instalação).
- `desktop/src/localSession.ts` (novo), `desktop/src/chatStream.ts`,
  `desktop/src/auth/AuthContext.tsx`, `bauer/static/` (bundle).
- Testes: `tests/test_server_body_limit_asgi.py`,
  `desktop/src/localSession.test.ts`. Docstring de
  `tests/test_server_body_limits.py` atualizada.
- Provas: `docs/audits/2026-10-12/repro/browser_logout_storage.py`,
  `docs/audits/2026-10-12/repro/bauer_web_body_limit_repro.py`.
  `repro/README.md` não foi editado, para não conflitar com os tracks
  paralelos. Os comandos estão no SPEC.
