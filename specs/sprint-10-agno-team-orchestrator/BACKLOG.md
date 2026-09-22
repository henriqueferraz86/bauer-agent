# BACKLOG — Agno Team Orchestrator

## 1. Contrato e dependências

Status: concluído

- [x] Criar SPEC e ARCHITECTURE.
- [x] Tornar `agno`, `sqlalchemy` e `openai` dependências padrão.
- [x] Definir o contrato do orquestrador.

## 2. Orquestrador

Status: concluído

- [x] Montar membros e supervisor a partir dos registries.
- [x] Executar `Team.run()` com modo `coordinate`.
- [x] Aplicar concorrência e orçamento.
- [x] Publicar eventos e devolver resultado normalizado.

## 3. Kernel e CLI

Status: concluído

- [x] Integrar execução do time ao Kernel.
- [x] Adicionar `bauer runtime teams run`.
- [x] Manter `delegate` compatível.

## 4. Testes

Status: concluído

- [x] Builder e validações.
- [x] Modelo Agno offline e streaming.
- [x] Policy, orçamento e concorrência.
- [x] CLI e suíte completa (7.468 passaram, 22 foram pulados).

## 5. Beelink e documentação

Status: pendente

- [ ] Atualizar lock e instalar dependências.
- [ ] Smoke test do time padrão na Beelink.
- [ ] Registrar comandos de operação e limitações.
