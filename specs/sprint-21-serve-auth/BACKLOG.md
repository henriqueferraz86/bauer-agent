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

## Condições de STOP

- não implementar cadastro aberto sem bootstrap;
- não enviar a API key para o frontend;
- não persistir senha, sessão ou ID token em texto puro;
- não habilitar Google sem validação de `aud`, assinatura e `email_verified`;
- não avançar ao próximo bloco se os testes do bloco atual falharem.
