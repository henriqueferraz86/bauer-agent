# SPEC — Sprint 34: cancelamento efetivo no Telegram

Plano 061, etapa 2. Pré-requisito: sprint 33 entregue e gates completos exit 0
em `6f68035`. Base inclui também master `55be7dc` (instalação de tools padrão).

## Objetivo e escopo

`/cancelar [run_id]` privado solicita interrupção somente de execução do mesmo
operador/chat/projeto. Com mais de uma execução, devolver escolhas explícitas.
Timeout solicita interrupção, mantendo lock/contexto/custódia até a saída real.
Sinal cooperativo interrompe fronteiras de LLM, ferramentas, retry, aprovação,
compressão e subprocessos gerenciados. Preservar custo e efeitos já realizados;
não prometer encerrar HTTP bloqueado ou desfazer efeitos anteriores.

Fora do escopo: painel de atividade (35), aprovações por botão (36), `/loop`
e inbox durável (37), bot real e implantação (38). Gateway usa native/bridge;
não anunciar cobertura de um AgnoAdapter que não está conectado a esse canal.

## Contrato de estado

Uma solicitação ao worker vivo instala sinal; apresentação mostra
`cancelamento solicitado`. O executor reporta `cancelled` com métricas após
sair e o Kernel declara o terminal. Não chamar `Kernel.cancel` antecipadamente
no worker vivo, pois ele libera reserva/estado antes de encerrar o trabalho.
Sua API permanece disponível para execuções sem worker e cleanup idempotente.

Um contexto ainda ocupado não pode ser despejado por timeout ou `/new` para
iniciar trabalho concorrente. O worker externo mantém toda custódia do Kernel;
o transporte pode devolver aviso enquanto ele termina.

## Skills e time

Skill spec-driven-project-setup; AGENTS.md. `telegram_runtime`: backend e
registro de execuções; `telegram_projects`: execution_control e fronteiras
de agente/router/contexto; `telegram_review`: processos e isolamento de árvore;
root: comandos Telegram, integração, testes/revisão e gates.

## Aceite

- Cancelar durante LLM/retry/aprovação bloqueia todo novo LLM/tool.
- Batch de ferramentas interrompido preserva logs de efeitos já executados.
- A sessão permanece ocupada enquanto worker bloqueado ainda vive.
- Custo final chega ao Kernel mesmo após aviso de timeout do transporte.
- Subprocesso gerenciado e seus filhos param; outra run permanece intacta.
- Controles recusados em grupos/owner errado/projeto errado/run alheia.
- Sem controle ativo, contratos de CLI/web/providers existentes preservados.
- Config/budget/kill-switch reais não são alterados. Gates completos e testes
  herméticos, Linux/Windows, antes de avançar.
