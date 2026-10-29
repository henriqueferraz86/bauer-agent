# SPEC — Inicialização confiável do Gateway após reinício

## Problema

O início em background tratava qualquer processo com o mesmo PID salvo como
se fosse o Gateway. Em Docker, PIDs podem ser reutilizados após reiniciar o
container; o `ollama pull` no boot podia ocupar o PID antigo e impedir o
Telegram habilitado de iniciar.

## Objetivo

Ao iniciar o Bauer, reusar apenas um processo que seja realmente
`bauer.cli gateway start`; se o PID persistido pertencer a outro processo ou
não existir, limpar o estado obsoleto e iniciar o Gateway.

## Escopo

- Compartilhar a validação da identidade do processo entre o CLI e o
  `GatewayServiceManager`.
- Cobrir a decisão com testes de linha de comando.
- Manter o início automático dos canais habilitados no `start.sh`.

## Fora de escopo

- Adicionar suporte a um canal Instagram.
- Alterar credenciais ou destinos de canais.

## Critérios de aceite

- Um PID reutilizado por processo não Gateway não impede o início.
- Um processo Gateway real continua evitando inicialização duplicada.
- Gateway habilitado volta a iniciar no boot do container.

## Skills obrigatórias

- `spec-driven-project-setup`

## Sub-agents recomendados

- Nenhum; correção pequena no ciclo de início de processo.

## Plano de validação

- Testes unitários para identificar e rejeitar linhas de comando.
- Reiniciar o container Bauer e confirmar Gateway/Telegram ativos.
