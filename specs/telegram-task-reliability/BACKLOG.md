# Backlog — confiabilidade Telegram

- [x] Implementar normalização estrita de pseudo tool calls no executor nativo.
- [x] Impedir resposta silenciosa no transporte Telegram.
- [x] Roteamento de pedidos explícitos de equipe ao AgnoTeamOrchestrator sob
  Kernel e contexto de projeto existentes.
- [x] Incluir contexto documental do projeto no prompt da equipe e bloquear
  criação AppFactory para fases de projetos já selecionados.
- [x] Adicionar testes unitários herméticos e revisar validações do repositório.
- [x] Executar testes focados e gates frozen no Windows. Na suíte completa
  paralela, 10 casos alheios a esta mudança falharam por concorrência; os casos
  falhos passaram quando reexecutados serialmente.
- [x] Publicar na master, aplicar a imagem `2ab5dd2` na Beelink e confirmar
  health e Gateway Telegram ativo; volumes do projeto preservados.

Nota da investigação: `TASKS.md` na raiz mantém a Fase 11 antiga como TODO,
mas também registra a frente 11–14 como DONE. `investimentos-os/docs/PROGRESS.md`
e `DECISIONS.md` registram trabalho e conclusão posterior da Fase 11. Nenhum
arquivo do Investimentos OS foi alterado; o time deve expor essa divergência
antes de executar uma fase possivelmente concluída.
