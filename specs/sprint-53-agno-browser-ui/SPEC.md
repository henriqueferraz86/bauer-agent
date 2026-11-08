# SPEC — Sprint 53: browser e testes de interface (plano 057, sprint "21")

Depende da sprint 50 (ponte). Executada como 53.

## Problema

O Bauer já tem uma sessão Playwright governada pelo ToolRouter
(`bauer/tools/browser.py`: uma página por router, thread dedicada), mas sem
política de domínio, sem bloqueio de download e sem defesa SSRF em
subrecursos. O SDK Agno traz outros backends (Browserbase na nuvem, AgentQL
com Playwright próprio, `webbrowser` abrindo o navegador do desktop).

## Decisão de backend

**Um só backend: o Playwright do Bauer.** Browserbase/AgentQL criariam sessões
concorrentes e credenciais duplicadas ⇒ `incompatible` com motivo.
WebdriverIO não é usado (nenhuma necessidade não atendida pelo Playwright).

## Escopo

`bauer.browser` (capability Bauer-native) com perfis configurados em
`agno_capabilities.yaml`:

1. Perfil = allowlist de domínios (`app.example.com`, `*.example.org`);
   https por padrão; `allow_http`/`allow_localhost` opcionais e explícitos.
2. Guardas instaladas **na página** (route handler): navegação (digitada,
   clicada ou redirecionada) fora do perfil é abortada; requisição a IP
   privado/loopback/link-local/metadata é abortada; URL com credenciais
   embutidas é recusada.
3. Downloads cancelados (`page.on("download") → cancel`).
4. Depois de cada ação, a URL atual é re-checada; saiu do perfil ⇒ sessão
   fechada e `ToolError`.
5. Ações: `navigate`, `snapshot`, `close` (leitura) e `click`, `type_text`,
   `press` (`write`, operação `os.ui_control` ⇒ regra padrão pede aprovação
   humana por chamada).
6. Cancelamento/erro inesperado fecha página, contexto, Playwright e a thread
   dedicada.
7. Auditoria registra só **domínio** e decisão (`policy.evaluated`), nunca
   DOM, texto, screenshot, query string ou texto digitado.

## Fora de escopo

- Perfis autenticados (cookies/storage state): recusados na configuração até
  existir contexto de browser isolado por perfil (sessão compartilhada
  vazaria login entre perfis).
- Screenshots/visão como capability (já existem como tools Bauer; não
  expostas aqui para não registrar conteúdo de página).

## Decisões (sem operador — opção mais segura)

- Interações exigem aprovação pela policy padrão; operador pode relaxar com
  regra própria para `os.ui_control`.
- Passos internos chamam as implementações `browser_*` existentes na thread
  dedicada, sem segunda checagem de contexto (o grant e a policy já foram
  aplicados à chamada externa).

## Critérios de aceite

- [x] Página permitida passa; domínio não permitido nunca chega ao `goto`.
- [x] Redirecionamento para fora do perfil é abortado na página.
- [x] Download cancelado; subrecurso interno bloqueado.
- [x] Sair do perfil após ação fecha a sessão; cancelamento fecha a sessão.
- [x] Interação sem aprovação não executa.
- [x] Auditoria sem conteúdo de página nem query string.
- [x] Suíte e Ruff verdes.
