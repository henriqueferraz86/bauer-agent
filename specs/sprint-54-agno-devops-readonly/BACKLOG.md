# BACKLOG — Sprint 54: DevOps read-only

- [x] `bauer.docker`: settings (hosts, socket, allowlist), transporte só-GET,
      list/inspect/logs/images/info com redação.
- [x] `bauer.aws`: settings (conta, perfil, account_id, regiões, clusters),
      cliente read-only por allowlist de operações, verificação STS.
- [x] `bauer.kubernetes`: contextos nomeados, namespaces em allowlist,
      pods/deployments/logs redigidos.
- [x] `agno.docker`, `agno.aws_lambda`, `agno.aws_ses` `incompatible`.
- [x] Testes `tests/test_agno_devops_capabilities.py` (MockTransport e
      fábricas fake; tentativa de POST/DELETE sem I/O; conta errada).
- [x] Gates: suíte completa, Ruff bloqueante e de estilo; nenhum
      `except: pass` novo (catraca P5).
