# SPEC — Telegram via AgentOS HTTP

## Problema e objetivo

O adapter `agno` do Telegram executa o SDK no processo local, não o AgentOS remoto. Criar uma opção explícita `agentos_http` que transporte turnos ao serviço remoto, mantendo o Gateway e o Kernel como fronteiras de controle e sem mudar os defaults dos outros canais. O objetivo final e os gates são definidos no plano de 13/11/2026 anexado à solicitação; esta spec não afirma que já foram cumpridos.

## Escopo

- Diagnóstico efetivo da Beelink e contrato OpenAPI da versão instalada, sem registrar segredos.
- Autenticação, isolamento e política de ferramentas no executor remoto.
- Cliente HTTP independente do SDK, descoberta, agente e time, streaming, cancelamento e continuação conforme contrato verificado.
- Roteamento Telegram explícito, identidade e sessão isoladas, persistência transacional e reconciliação sem duplicar efeitos.
- Aprovação, cancelamento, recuperação, métricas, mídias e execução longa com evidência de paridade.
- Canary read-only, rollback verificado e, somente após os gates, corte do Telegram para HTTP.

## Fora de escopo

- Remover Kernel ou executor Native de CLI, Web ou Scheduler.
- Presumir suporte a workflows sem registro no AgentOS.
- Migrar histórico implicitamente, aceitar IDs ilustrativos como catálogo real ou disponibilizar escrita sem governança equivalente comprovada.

## Skills obrigatórias e agentes

- `spec-driven-project-setup`: rastreabilidade e tarefas pequenas.
- Agentes: diagnóstico/infraestrutura para Beelink e contrato; backend para cliente e roteamento; segurança para autenticação, escopo e aprovação; testes/revisão para gates e regressões. Os responsáveis são distribuídos conforme dependências e disponibilidade.

## Requisitos e critérios de aceite

1. AgentOS autenticado e acessível apenas na rede apropriada; catálogo, versões, rotas, backups e fronteiras de disco/rede verificados antes do rollout.
2. Pedido Telegram autorizado encaminhado a agente ou time real, com identificação e sessão por usuário, chat, projeto e geração; `/new` cria nova geração.
3. Gateway mantém correspondência auditável entre runs e trata resultados e eventos por estado real; interrupção não provoca reenvio cego nem fallback após admissão possível.
4. Ferramenta proibida é bloqueada remotamente; escrita ocorre uma única vez após aprovação explícita e usuários não controlam runs uns dos outros.
5. Cancelamento é confirmado por estado terminal; recuperação após restart reconcilia sem repetir efeitos; uso sem evidência é desconhecido, não zero.
6. Streaming, anexos e tarefas longas são validados pelas capacidades reais do serviço; nenhum caminho anuncia sucesso antes de conclusão terminal.
7. Comandos existentes e fluxo nativo permanecem compatíveis. `/model` não altera modelo global acidentalmente.
8. Canary com pelo menos 20 turnos read-only e rollback exercitado precedem mudança do default do Telegram. A retirada do Native do canal exige todos os gates do plano.

## Validação

Usar mocks herméticos para contrato e erros HTTP/SSE, sessões, concorrência e idempotência; testes E2E de aprovação/cancelamento/recuperação e regressões existentes; smoke tests autenticados e read-only no ambiente real. Executar `uv sync --frozen --extra dev`, `uv run pytest tests/ -q --tb=short` e ambos os comandos Ruff exigidos em AGENTS.md. CI e testes específicos de Windows devem passar conforme aplicável. Registrar resultados e limitações no backlog antes de avançar entre fases.
