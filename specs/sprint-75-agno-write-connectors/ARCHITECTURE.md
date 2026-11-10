# ARCHITECTURE — Sprint 75

```text
modelo → Function bauer_<conector>__<ação>(args)
  → ponte: policy habilitada? (senão recusa)
  → ponte: separa approval_id (só ALWAYS_ASK) → preflight(args):
        --local · allowlist · limites · broadcast · argumento desconhecido
        · material secreto · credencial presente        (sem rede, sem aprovação)
  → ToolRouter.execute
      → PolicyEngine.evaluate("integration.write") → piso ⇒ ask
      → reason = approval_summary(args)  (uma linha, truncada, redigida)
      → enforce: approvals.request(suspend_run) → espera humana → authorize
        audit:   waiting_approval (sem auto-aprovação) → retry com approval_id
      → dry_run? ⇒ DryRunResult (nada sai)
  → invoke: remove approval_id, recusa segredo, fn(**args)
  → ação: valida de novo (--local/allowlist), resolve token
      → execute_once(ledger, chave):
           done     ⇒ replay
           pending  ⇒ lookup(marcador) ⇒ achou: replay
                                      ⇒ não achou: erro (Slack/GitLab)
                                                    ou reenvia mesmo id (Linear)
           ausente  ⇒ reserva O_EXCL ⇒ perform()
                        NotSent   ⇒ libera reserva + ToolError
                        Uncertain ⇒ mantém pending + ToolError
                        ok        ⇒ done(resultado)
```

## Módulos

- `agno_capabilities/families/write_common.py` (novo): `http_client`
  (redirect desligado, `shared_ssl_context`, seam `_TRANSPORT`), `send`/`read`
  com classificação `NotSent`/`Uncertain`, `classify_status`,
  `ensure_local_allowed`, `safe_summary`/`one_line`, `idempotency_key`
  (escopo = run, senão sessão), `WriteLedger`, `execute_once`.
- `families/slack_write.py`, `families/linear_write.py`,
  `families/gitlab_write.py` (novos): settings estritos, validação,
  preflight, ação, resumo, factory, `register_policy`/`register_factory`/
  `enable_approval_flow`.
- `families/__init__.py`: importa os três.
- `agno_capabilities/bridge.py`: `approval_id` como metadado de transporte
  para operações `ALWAYS_ASK`; falha do scanner de segredo agora recusa.

Ledger: `<policy_root>/agno_capabilities/write_ledger/<conector>/<chave>.json`
(chave = 32 hex de `sha256([escopo, conector, alvo, conteúdo…])`).

Nada mudou em `tool_router.py`, `core/policy/`, `catalog.py`,
`catalog_coverage.py`, `sdk_snapshot.json`, `pyproject.toml` ou `uv.lock`.
