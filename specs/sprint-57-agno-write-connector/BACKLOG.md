# BACKLOG — Sprint 57: primeiro conector com escrita

- [x] Escolher conector e ação de baixo impacto (GitHub create_issue).
- [x] Capability separada, desabilitada até configuração.
- [x] Piso `ask` para `integration.write` no PolicyEngine; audit não
      auto-aprova; policy off recusa.
- [x] Resumo de aprovação (alvo/efeito/identidade).
- [x] Preflight local antes da aprovação.
- [x] Idempotência por run (ledger + marcador remoto).
- [x] Erros remotos explícitos.
- [x] Testes `tests/test_agno_write_connector.py` (Kernel/approvals reais,
      humano simulado por thread, MockTransport).
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.
