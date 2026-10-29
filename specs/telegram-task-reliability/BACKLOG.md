# Backlog — confiabilidade Telegram

- [x] Implementar normalização estrita de pseudo tool calls no executor nativo.
- [x] Impedir resposta silenciosa no transporte Telegram.
- [x] Roteamento de pedidos explícitos de equipe ao AgnoTeamOrchestrator sob
  Kernel e contexto de projeto existentes.
- [x] Adicionar testes unitários herméticos e revisar validações do repositório.
- [x] Executar testes focados e gates frozen no Windows. Na suíte completa
  paralela, 10 casos alheios a esta mudança falharam por concorrência; os casos
  falhos passaram quando reexecutados serialmente.
- [x] Publicar na master, aplicar a imagem do commit na Beelink e confirmar
  health, Gateway Telegram ativo e persistência dos volumes.
