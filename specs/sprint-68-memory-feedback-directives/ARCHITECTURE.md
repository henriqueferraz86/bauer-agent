# ARCHITECTURE — Sprint 68

```text
/thumbsup|/thumbsdown [motivo]  (sprint 65)
  └─ FeedbackStore  <ws>/memory/consolidation/feedback.sqlite3
bauer memory directives propose
  └─ DirectiveStore.propose
       FeedbackStore.list(since = now - 30d)        (só deste workspace)
       agrupa por (avoid|prefer, fingerprint)
       ≥3 sinais e ≥2 sessões → rascunho (status=draft)
       grava <ws>/memory/consolidation/directives.json (atômico, confinado)
bauer memory directives approve <id> [--ttl-days] [--text] [--yes]
  └─ draft → approved (approved_at, expires_at)
bauer memory directives reject|revoke <id>
LocalMemoryProvider.system_prompt_block()
  └─ directives_prompt_block(workspace) → só approved && expires_at > agora
```

## Estados

```text
draft ──approve──▶ approved ──revoke──▶ revoked
  │                    │
  └──reject──▶ rejected └── (expires_at passou: inativa, continua approved)
```

Uma recusada/revogada nunca volta para `draft`. Se surgirem sinais novos
depois de `closed_at`, nasce outra diretiva (`dir-<fp>-<kind>-<n+1>`) e a
antiga continua no histórico.

## Segurança do loop

- Human-in-the-loop obrigatório: nenhum caminho escreve `approved` além do
  `approve` explícito. O consolidador (sprints 66/67) não cria diretivas.
- Contra viés de um único momento ruim: o limiar de 3 sinais em 2 sessões,
  o decay de 30 dias e a expiração obrigatória.
- Escopo:
  - o arquivo fica dentro do workspace;
  - registros com outro `workspace_id` são ignorados na leitura;
  - `memory/consolidation` não pode ser symlink.
- Texto injetado:
  - vem de feedback já redigido, ou do `--text` do usuário sanitizado;
  - tem tamanho limitado;
  - o bloco é rotulado "aprovadas pelo usuário (revogáveis)".
