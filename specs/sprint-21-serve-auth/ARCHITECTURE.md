# Arquitetura — Autenticação do frontend do `bauer serve`

## Visão geral

A autenticação da SPA passa a usar o padrão Backend-for-Frontend. O navegador
recebe somente um identificador de sessão opaco em cookie `HttpOnly`; a
`BAUER_SERVE_API_KEY` nunca é devolvida pelo servidor e deixa de ser persistida
no frontend.

```text
cadastro único ── API key de bootstrap ──┐
                                         v
Google Identity Services ── ID token ──> AuthService ──> SQLite
e-mail + senha ─────────────────────────>    │          users/sessions
                                             v
browser <── cookie HttpOnly + cookie CSRF ─ sessão
   │
   └── fetch same-origin + X-CSRF-Token ──> _verify_key_or_session

CLI / integração ── X-API-Key ───────────> _verify_key_or_session
```

## Componentes

### `bauer/web_auth.py`

- `WebAuthStore`: SQLite, schema e transações do administrador/sessões;
- `PasswordService`: Argon2id e rehash oportunista;
- `WebAuthService`: setup, login, Google, recovery e revogação;
- tokens de sessão aleatórios; somente SHA-256 do token vai ao banco;
- verificador Google injetável para testes.

O banco fica em `<runtime_root>/web_auth.sqlite3`. No Compose, `runtime_root`
está sob `/app/memory/runtime`, já persistido pelo volume `bauer_memory`.

### Endpoints FastAPI

- `GET /auth/state` — público e sem segredos;
- `POST /auth/setup` — e-mail/senha/bootstrap;
- `POST /auth/login` — e-mail/senha;
- `POST /auth/google` — Google ID token e bootstrap somente no primeiro uso;
- `POST /auth/logout` — sessão + CSRF;
- `POST /auth/recover` — bootstrap + nova senha.

As respostas de sucesso criam `bauer_session` (`HttpOnly`) e `bauer_csrf`
(legível pela SPA). A sessão no banco guarda hash do token, hash do CSRF,
expiração, criação e último uso.

### Autorização existente

`_verify_key` evolui para aceitar:

1. API key em `X-API-Key` ou Bearer, com `hmac.compare_digest`; ou
2. cookie de sessão válido.

Quando a autenticação veio por cookie, o middleware/depêndencia exige CSRF nas
operações mutáveis. `/health`, assets e `/auth/*` seguem regras próprias.

### Frontend React

- `AuthProvider` consulta `/auth/state` no boot;
- `AuthGate` renderiza setup, login ou aplicação;
- `AuthScreen` oferece cadastro/login local e botão Google quando configurado;
- `fetch` usa `credentials: "same-origin"` e `X-CSRF-Token` quando presente;
- em 401, estado volta para login;
- `bauer.apiKey` é removida depois de autenticar;
- Config deixa de exibir o campo manual de API key e passa a mostrar conta,
  logout e recuperação.

## Persistência

```text
admin
  id = 1 (PK + CHECK)
  email_normalized (UNIQUE)
  password_hash (nullable para Google-only)
  google_sub (nullable, UNIQUE)
  display_name
  created_at / updated_at

sessions
  token_hash (PK)
  admin_id (FK)
  csrf_hash
  created_at / expires_at / last_seen_at
```

SQLite usa WAL, foreign keys e transação `BEGIN IMMEDIATE` no setup para que
duas requisições concorrentes não criem dois administradores.

## Google

Google Identity Services entrega um ID token ao browser. O backend valida
assinatura, `aud`, `iss`, expiração e `email_verified` com a biblioteca oficial
`google-auth`. O token não é persistido. Apenas `sub`, e-mail verificado e nome
são gravados.

Não são solicitados access token, refresh token ou permissões de APIs Google.

## Decisões técnicas

- sessão opaca em vez de JWT: revogação imediata e menos segredo no cliente;
- cookie `HttpOnly` em vez de `localStorage`: reduz impacto de XSS;
- Argon2id via `argon2-cffi`: algoritmo memory-hard e API madura;
- Google ID token via GIS: autenticação simples sem client secret;
- API key preservada: compatibilidade de automações e bootstrap seguro;
- um administrador fixo: corresponde ao produto single-operator atual.

## Alternativas consideradas

- injetar literalmente a API key no frontend após login: rejeitada porque
  reintroduz o segredo de longa duração no JavaScript;
- JWT no navegador: rejeitado por revogação e exposição;
- authorization code OAuth com refresh token: desnecessário, pois não haverá
  acesso a APIs Google;
- cadastro aberto do primeiro visitante: rejeitado por risco de takeover.

## Observabilidade

- eventos estruturados sem e-mail completo, senha, token ou API key;
- contadores de setup/login/logout/falha/rate-limit;
- logs de falha Google em DEBUG com dados sensíveis redigidos;
- `/auth/state` permite diagnóstico sem revelar identidade quando deslogado.

## Migração e compatibilidade

- sem API key, o serve local continua aberto como hoje;
- com API key e sem admin, a SPA mostra setup;
- clientes externos com header não mudam;
- build novo remove a chave legada do navegador após sessão válida;
- rollback mantém o header funcionando e não apaga o banco.
