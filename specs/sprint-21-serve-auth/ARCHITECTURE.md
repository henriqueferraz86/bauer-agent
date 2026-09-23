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

Settings ── POST start ──> OpenAIBrowserAuthBroker ── URL auth.openai.com
   ^                                  │
   │ polling status                   ├── PKCE/state somente em memória
   │                                  └── callback localhost:1455
   └──────── estado sem tokens <────────── TokenStore criptografado
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

### `bauer/openai_browser_auth.py`

- controla no máximo uma transação OAuth pendente por processo;
- inicia um callback HTTP mínimo em `127.0.0.1:1455` por padrão e
  `0.0.0.0:1455` no Compose;
- usa as primitivas PKCE/troca/persistência de `AuthManager`;
- valida `state` e expiração antes de trocar o código;
- entrega ao browser apenas uma página de sucesso/erro que fecha o popup;
- expõe ao Desktop API somente status sanitizado.

### Endpoints de provider no Desktop API

- `GET /api/auth/openai/status` — estado sanitizado;
- `POST /api/auth/openai/start` — inicia/reinicia a transação e devolve a URL;
- `POST /api/auth/openai/logout` — remove a credencial persistida.

Todos herdam a autenticação e o CSRF do router `/api`. O callback OAuth não
passa pela SPA; a proteção dele é o `state` aleatório ligado ao PKCE e com TTL.

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

## OpenAI/ChatGPT experimental

A API pública OpenAI documenta API key ou workload identity. O fluxo existente
de login ChatGPT/Codex no Bauer é experimental e permanece identificado assim
na interface. A SPA abre a URL, mas tokens e verifier ficam exclusivamente no
backend. Após o callback, `AuthManager` continua sendo a fonte usada por
`_build_client` quando o operador seleciona provider `openai`.

No Docker, `BAUER_HOME=/app/memory/bauer-home` coloca `auth.json` e `.auth_key`
no volume `bauer_memory`; a porta 1455 é publicada para o callback no browser
da mesma máquina. Execução remota continua usando API key ou o CLI headless.

### Seleção automática e latência

Depois de `complete_oauth()` gravar o token, o Desktop API solicita a troca de
modelo no estado vivo do Serve para `provider=openai` e
`model=gpt-5.6-luna`. A seleção é best-effort: se a conta não aceitar o Luna,
o token continua salvo e o frontend informa a falha sem fazer logout.

`ChatGPTBackendClient` envia `reasoning: {effort: "high"}` para o Luna. O campo
é deliberadamente aplicado no backend, nunca pelo JavaScript, e só afeta o
cliente ChatGPT OAuth; providers API-key e modelos diferentes não são
alterados.

### Executor global do Server

`/api/runtime/mode` mantém uma escolha global persistida em
`serve-runtime-mode.json`: `bauer_native` preserva o loop histórico do Serve e
`agno` executa os próximos turnos pelo `AgnoRuntimeAdapter`, sob a mesma
admissão/política do Kernel. O AgentOS também expõe `/api/models/catalog` e
`/api/models/select`; o overlay do Agent UI mostra o catálogo e troca o modelo
de todos os agentes/times. Para OpenAI OAuth, o adapter materializa um modelo
Agno sobre `ChatGPTBackendClient`, reutilizando a sessão do browser sem API key
pública. A ponte atual é texto/streaming e não anuncia tools nativas.

## Decisões técnicas

- sessão opaca em vez de JWT: revogação imediata e menos segredo no cliente;
- cookie `HttpOnly` em vez de `localStorage`: reduz impacto de XSS;
- Argon2id via `argon2-cffi`: algoritmo memory-hard e API madura;
- Google ID token via GIS: autenticação simples sem client secret;
- API key preservada: compatibilidade de automações e bootstrap seguro;
- um administrador fixo: corresponde ao produto single-operator atual.
- broker separado da SPA: nenhum token OpenAI transita pelo JavaScript;
- callback 1455 preservado: é o redirect URI esperado pelo client público já
  usado pelo fluxo CLI atual;
- `TokenStore` usa `get_bauer_home()`: respeita `BAUER_HOME` e mantém o mesmo
  default `~/.bauer` fora do Docker.

## Alternativas consideradas

- injetar literalmente a API key no frontend após login: rejeitada porque
  reintroduz o segredo de longa duração no JavaScript;
- JWT no navegador: rejeitado por revogação e exposição;
- authorization code OAuth com refresh token: desnecessário, pois não haverá
  acesso a APIs Google;
- cadastro aberto do primeiro visitante: rejeitado por risco de takeover.
- redirect no `/api` da porta 8000: rejeitado porque o client OAuth existente
  espera `localhost:1455/auth/callback`;
- executar `login_oauth()` bloqueante dentro de request: rejeitado porque prende
  worker e tenta abrir browser no contêiner;
- devolver token à SPA: rejeitado por exposição desnecessária.

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
