# Backlog Sprint 21 — Autenticação do frontend

## 1. Especificação e branch

Status: concluído

- [x] Definir escopo, ameaças e compatibilidade.
- [x] Definir arquitetura de sessão e Google.
- [x] Criar branch `codex/sprint-21-serve-auth`.

## 2. Store e serviços de autenticação

Status: concluído

- [x] Criar schema SQLite singleton.
- [x] Implementar Argon2id, sessão opaca e CSRF.
- [x] Implementar setup/login/logout/recovery/Google.
- [x] Criar testes unitários, incluindo corrida de setup.

## 3. Integração FastAPI

Status: concluído

- [x] Adicionar config estrita e variáveis de ambiente.
- [x] Criar endpoints `/auth/*`.
- [x] Aceitar API key ou sessão nas rotas protegidas.
- [x] Aplicar cookies, CSRF e rate limit de autenticação.
- [x] Preservar comportamento sem API key.

## 4. Frontend

Status: concluído

- [x] Criar `AuthProvider`, `AuthGate` e telas de setup/login.
- [x] Integrar Google Identity Services opcional.
- [x] Atualizar cliente HTTP para cookie + CSRF.
- [x] Remover armazenamento manual da API key.
- [x] Adicionar logout/conta e testes Vitest.

## 5. Dependências, documentação e Docker

Status: concluído

- [x] Adicionar `argon2-cffi` e `google-auth` ao extra server e lock.
- [x] Documentar configuração e Google Client ID.
- [x] Garantir persistência do banco no volume existente.
- [x] Atualizar `.env.example` e `config.yaml.example`.

## 6. Gates e revisão

Status: concluído

- [x] Rodar testes focados de backend/frontend.
- [x] Buildar a SPA para `bauer/static`.
- [x] Rodar suíte, Ruff crítico e Ruff informativo.
- [x] Revisar segurança contra a SPEC.
- [x] Smoke test Docker no Windows.

## 7. OpenAI via browser em Settings

Status: concluído

- [x] Validar limites do fluxo na documentação oficial OpenAI.
- [x] Atualizar SPEC e arquitetura antes do código.
- [x] Extrair primitivas PKCE/complete reutilizáveis do `AuthManager`.
- [x] Implementar broker local com callback 1455, state e TTL.
- [x] Adicionar endpoints protegidos de status/start/logout.
- [x] Adicionar card e popup/polling em Settings.
- [x] Persistir `BAUER_HOME` no volume Docker e publicar callback.
- [x] Criar testes backend/frontend/Docker e documentação.
- [x] Rodar gates e smoke test Windows.

## Condições de STOP

- não implementar cadastro aberto sem bootstrap;
- não enviar a API key para o frontend;
- não persistir senha, sessão ou ID token em texto puro;
- não habilitar Google sem validação de `aud`, assinatura e `email_verified`;
- não apresentar o OAuth ChatGPT experimental como autenticação oficial da API;
- não entregar token, verifier ou ID token OpenAI à SPA;
- não avançar ao próximo bloco se os testes do bloco atual falharem.

## 8. Diagnóstico de latência e seleção automática OpenAI

Status: concluído

- [x] Confirmar modelo ativo, fonte OAuth, tempos de execução e ausência de
      fallback/retry nos runs do Windows.
- [x] Comparar comportamento com a documentação oficial de `gpt-5.6-luna` e
      `reasoning.effort`.
- [x] Enviar esforço baixo para Luna no backend ChatGPT.
- [x] Selecionar `openai/gpt-5.6-luna` automaticamente ao concluir OAuth.
- [x] Exibir aviso sanitizado quando Luna não estiver disponível na conta.
- [x] Cobrir payload, seleção e fallback com testes.
- [x] Rebuildar o Docker e medir novamente o tempo de resposta: HTTP 200,
      primeiro byte em ~2,49 s e conclusão em ~2,49 s.
