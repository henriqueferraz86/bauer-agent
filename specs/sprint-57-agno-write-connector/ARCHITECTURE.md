# ARCHITECTURE — Sprint 57

```text
modelo → Function bauer_github_issues__create_issue(repo, title, body, labels)
  → ponte: policy habilitada? (senão recusa) → preflight (allowlist/limites)
  → ToolRouter.execute
      → PolicyEngine.evaluate("integration.write") → piso ⇒ ask
      → reason = approval_summary(args)          (alvo, efeito, identidade)
      → enforce + run: approvals.request(suspend_run) → espera humana
        → approved: authorize(digest exato, uso único, resume_run)
        → denied/expired/timeout: ToolError
      → audit: NÃO auto-aprova integration.write ⇒ waiting_approval
  → create_issue: ledger hit? → busca marcador no GitHub? → POST /issues
  → ledger grava {repo, number, url}
```

Mudanças compartilhadas (mínimas):

- `core/policy/engine.py`: `ALWAYS_ASK_OPERATIONS` + `_floor()`.
- `tool_router.py`: `approval_summary` opcional em `register_capability_tool`
  (dict por instância); exceção do auto-approve do modo audit para
  `ALWAYS_ASK_OPERATIONS`.
- `agno_capabilities/bridge.py`: `ActionImpl.approval_summary` e
  `ActionImpl.preflight`; recusa de escrita com policy desligada.
