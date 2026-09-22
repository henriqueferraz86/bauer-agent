# SPEC — Sprint 17: rastreabilidade e painel de agentes

## Problema

O Bauer possui eventos formais de agentes, mas o adapter Agno descarta eventos
de membros dos times. A tela Agents mostra somente definições estáticas e não
indica quando cada agente executou nem o que está fazendo.

## Objetivo

Persistir eventos de ciclo de vida e atividade dos agentes Agno no EventBus do
Kernel e mostrar estado atual e uso recente no frontend.

## Escopo

- Solicitar ao Agno o stream completo de eventos, preservando streaming de
  resposta final e os eventos dos membros.
- Normalizar início, atividade de resposta/ferramenta, conclusão, falha e
  delegação em eventos Bauer associados ao run e ao agente membro.
- Excluir conteúdo de mensagens, argumentos/resultados de ferramentas e
  raciocínio dos dados de telemetria.
- Acrescentar aos agentes do frontend status, execução atual, uso recente e
  timestamp da última utilização, com atualização periódica.
- Atualizar continuamente os eventos exibidos nos detalhes de uma execução
  ainda ativa.
- Documentar as oportunidades de tools Agno e os limites para uma integração
  gradual, baseada no catálogo oficial.

## Fora de escopo

- Controlar, pausar ou cancelar membros Agno individualmente.
- Gravar prompts, respostas, raciocínio, argumentos ou resultados de tools no
  histórico de observabilidade.
- Mudar seleção de agentes, políticas, permissões ou configuração de providers.
- Substituir o EventBus ou introduzir uma segunda base de rastreamento.

## Skills obrigatórias

- spec-driven-project-setup

## Sub-agents recomendados

- backend-implementer
- security-reviewer
- code-reviewer
- test-engineer

## Critérios de aceite

1. Runs Agno individuais continuam entregando seus deltas de resposta.
2. Runs de times Agno geram eventos persistidos com run, time e agente membro.
3. Eventos do frontend não incluem texto de conteúdo, raciocínio ou payloads de
   ferramentas.
4. A tela Agents diferencia agentes em execução dos ociosos e mostra uso
   recente sem exigir recarregar a página.
5. O timeline em Runs atualiza enquanto a execução selecionada está ativa e
   permanece consultável após terminar.
6. Falha na telemetria não interrompe execução governada pelo Kernel.

## Plano de validação

- Testes unitários da normalização dos eventos Agno e associação a membros.
- Testes do Kernel confirmando persistência dos eventos e ausência de payload
  sensível.
- Build TypeScript do frontend.
- Rodar os comandos de CI definidos no `AGENTS.md`.
