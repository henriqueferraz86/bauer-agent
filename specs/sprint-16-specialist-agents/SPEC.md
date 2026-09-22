# Sprint 16 — Bauer specialist agents

## Objetivo

Adicionar Bauer Architect, Security, Research, Docs e Data ao catálogo formal,
integrá-los ao Bauer Software Team e permitir que o fallback local selecione o
especialista adequado sem depender de uma chave Jev.

## Escopo

- Criar uma especificação formal Agno para cada um dos cinco especialistas.
- Adicionar os cinco agentes ao `bauer.software_team`, mantendo Product como
  coordenador e supervisão pelo Kernel.
- Limitar Bauer Security a ferramentas de leitura e pesquisa, sem escrita ou
  execução de shell.
- Encaminhar pedidos reconhecíveis ao especialista correspondente no fallback
  local.
- Expor `search_text` como ferramenta Agno apenas para agentes que precisam
  localizar evidências no workspace.
- Documentar responsabilidades, limites e formas de seleção.

## Fora de escopo

- Criar provedores, modelos ou credenciais específicos para os agentes.
- Habilitar Jev ou exigir API key.
- Alterar as permissões dos agentes Product, Dev, QA e DevOps existentes.
- Conceder ao Bauer Security acesso a segredos, shell ou ferramentas mutáveis.

## Critérios de aceite

1. O catálogo formal reconhece os cinco novos IDs e suas definições Agno.
2. `bauer.software_team` lista os nove agentes e mantém Product como supervisor.
3. O fallback escolhe Security, Research, Docs, Data ou Architect para pedidos
   claramente correspondentes, mesmo sem chave Jev.
4. Bauer Security recebe somente ferramentas de leitura e pesquisa; não recebe
   `write_file`, `run_command` nem ferramenta de delegação mutável.
5. Escritas feitas por Bauer Docs continuam passando pelo ToolRouter e pelas
   políticas de aprovação configuradas no Bauer.
6. As instruções e responsabilidades ficam documentadas para uso no chat,
   `bauer run` e runtime Agno.

## Plano de validação

- Conferir YAML e descoberta dos agentes/times pelo registro formal.
- Cobrir o roteamento local dos cinco perfis e as ferramentas atribuídas.
- Validar a construção das ferramentas Agno e os limites de escrita do Security.
- Rodar os gates do repositório antes de abrir PR.
