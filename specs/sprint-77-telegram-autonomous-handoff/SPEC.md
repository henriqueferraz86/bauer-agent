# SPEC — Sprint 77: o agente do Telegram decide quando abrir um /loop

Complementa o plano 061 (etapa 5, `/loop` autônomo no Telegram).

## Problema

Pedido do operador em 2026-11-11: "quero passar a demanda e o agent decidir
quando usar o /loop ou não".

Até aqui, trabalho longo pelo Telegram só começava com `/loop <objetivo>`
digitado. Uma mensagem comum rodava como turno de conversa, com prazo por
turno (`BAUER_GATEWAY_TURN_TIMEOUT`, padrão 120 s). Na Beelink, uma demanda
de implementação foi cortada aos 125 s, com 41 tools já executadas. Pedir
para "delegar aos agentes" também não resolvia: `delegate_task` local é uma
consulta de uma chamada, sem tools, que devolve um parecer.

## Comportamento

- Em turno de conversa **privada** do Telegram, o modelo recebe a tool
  `iniciar_trabalho_autonomo(objetivo)`. A descrição diz quando usá-la
  (trabalho de vários arquivos, testes, várias etapas, mais que alguns
  minutos) e quando não (perguntas, status, mudanças pequenas).
- A tool só registra o objetivo no `ExecutionControl` do turno. O `/loop`
  usa a mesma sessão e precisa da trava dela, que o turno ainda segura.
- Quando o turno termina sem cancelamento, o backend guarda o objetivo por
  sessão. Depois de entregar a resposta, o bridge chama
  `telegram_loop.start_loop(backend, msg, update_id, objetivo)`, o mesmo
  caminho do comando: admissão no Kernel com `autonomous=True`, política,
  aprovação, registro persistido antes do "iniciado", idempotência por
  `update_id` e entrega do relatório final na conversa.
- O loop aparece no painel como run em andamento (`/runs` do `serve` lê o
  mesmo runtime do gateway) e responde a `/atividade` e `/cancelar`.

## Limites

- Fora de turno privado do Telegram (CLI, web, grupos, Discord, dentro do
  próprio loop), a tool não entra no schema e é recusada se chamada.
- Turno cancelado ou que estourou o prazo não inicia loop.
- Se a sessão ainda estiver ocupada (mensagens na fila), `start_loop` responde
  que a conversa está ocupada e nada é admitido, como no comando.
- A tool está em `EFFECT_TOOLS` e em `MUTATING_TOOLS`. Sem isso, a
  deduplicação devolveria "agendado" num turno seguinte sem agendar nada.

## Validação

`tests/test_sprint77_autonomous_handoff.py`: disponibilidade por canal e
privacidade, registro do objetivo, turno cancelado, objetivo vazio, turno do
canal (nativo) entregando o objetivo uma vez, grupo recusado, bridge chamando
`start_loop` com `update_id`, ponta a ponta com `start_loop` real até o
relatório entregue, ponte de tools do Agno e repetição do mesmo objetivo em
turnos diferentes.

## Correções da revisão

A primeira versão guardava o objetivo por sessão, e quem consumia era o
próximo processamento da conversa. Isso abria três falhas: uma mensagem na
fila podia tomar o pedido e perdê-lo; um turno que estourava o prazo deixava
o pedido pendurado para qualquer mensagem posterior (inclusive `/new`)
disparar; e um `/cancelar` logo depois da chamada da tool não impedia o
loop. Agora:

- o pedido é guardado por `(sessão, update_id)` da mensagem que o fez e só é
  consumido pela thread que entrega a resposta dela;
- só a primeira mensagem de um lote, sem prazo estourado, pode guardar
  pedido. Num turno da fila ou atrasado, a resposta ganha um aviso de que o
  trabalho autônomo não foi iniciado;
- no consumo, o pedido é descartado se o turno foi cancelado ou estourou o
  prazo depois de registrá-lo;
- `/new` e `/clear` apagam pedidos pendentes da sessão.
