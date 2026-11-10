# BACKLOG — Sprint 75: conectores de escrita sob aprovação

- [x] Ler AGENTS.md, plano 067, plano 057 e specs 57/61/62/69.
- [x] Infra comum (`write_common.py`): client sem redirect, classificação
      NotSent/Uncertain, ledger com reserva exclusiva, `--local`, resumo seguro.
- [x] `bauer.slack_messages:post_message` (allowlist de canais por ID,
      broadcast bloqueado, marcador em `metadata`).
- [x] `bauer.linear_issues:create_issue` (mapas time/projeto/label → UUID,
      id da issue determinístico).
- [x] `bauer.gitlab_issues:create_issue` (`base_url` do operador validada,
      allowlist de projetos, marcador na descrição, `--local` só nesta máquina).
- [x] Ponte: `approval_id` como metadado de transporte em `ALWAYS_ASK`.
- [x] Ponte: falha do scanner de segredo fecha o portão.
- [x] Testes `tests/test_sprint75_agno_write_connectors.py` (MockTransport,
      Kernel/approvals reais, humano simulado).
- [x] Gates: suíte completa, Ruff bloqueante e de estilo, mypy.
- [ ] Pendências registradas no SPEC (catálogo × `--local`, texto de bloqueio
      dos toolkits SDK, DNS do `base_url`).
