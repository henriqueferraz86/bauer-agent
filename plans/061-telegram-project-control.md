# Plano 061: Telegram com projeto, atividade e controle de execução

## Status e objetivo

- **Status**: IN PROGRESS — execução autorizada em 2026-10-21; sprint 33 iniciada.
- **Data/base**: 2026-10-21, checkout `e22e1aa`.
- **Prioridade/esforço**: P1 / L, dividido em entregas pequenas.
- **Objetivo**: conversar com o Bauer pelo celular sabendo em qual projeto ele
  trabalha, acompanhar os agentes e controlar tarefas e aprovações com o mesmo
  Kernel, políticas e orçamento do runtime.
- **Dependências**: integração existente do gateway; registro de projetos;
  execução governada; eventos de agentes; persistência de aprovações.
- **Entregas relacionadas**: sprints 28–32; plano 056 excluiu aprovação remota
  do seu MVP, portanto esta integração complementa aquele trabalho.

Este arquivo é um plano de execução, não uma declaração de funcionalidade
entregue. Não ativar o bot nem iniciar tarefas reais durante o planejamento.

## Evidência e baseline

Verificado no checkout acima:

- `telegram_bridge.py` implementa long-polling, allowlist, processamento em
  threads, digitação, edição progressiva, mídia, transcrição e seleção de modelo.
- `channel_base.py` persiste histórico por chat, usa o workspace do config e
  encaminha turnos por `run_governed`. Não possui comandos dedicados para
  projeto, atividade do time, cancelamento ou loop autônomo.
- O timeout devolve uma resposta, mas a thread daemon pode continuar executando.
  Ele não equivale a cancelamento efetivo.
- O offset do Telegram é salvo após enfileirar updates em memória, antes da
  conclusão dos trabalhos; um crash nesse intervalo pode perder trabalho aceito.
- Já existem `projects_registry.py`, `ProjectRouterCache`,
  `build_chat_activity`, `ApprovalManager`, `approval_summary`,
  `continue_governed` e cancelamento do Kernel. Reutilizar esses contratos.
- As configurações locais e canônica verificadas têm Telegram desabilitado,
  sem token e sem usuários autorizados; nenhum serviço gateway foi encontrado
  ativo. Isso descreve a máquina inspecionada, não outras instalações.
- Os testes focados abaixo passaram com API e providers simulados. Não houve
  envio de mensagem real nem validação ponta a ponta de um bot.

```bash
uv sync --frozen --extra dev
uv run pytest tests/test_telegram_bridge.py tests/test_telegram_liso.py tests/test_channel_base.py tests/test_liso_backend.py tests/test_gateway_runtime.py tests/test_gateway_real.py tests/test_config_gateway.py -q --tb=short
```

Antes da implementação, conferir o drift desses componentes e registrar novo
baseline; outras correções de aprovação já existem neste checkout.

## Experiência desejada

Comandos propostos, mantendo os comandos atuais e seus aliases:

| Ação | Interface proposta | Comportamento |
|---|---|---|
| Escolher projeto | `/projetos` e botões; `/projeto` consulta o vínculo | Lista projetos permitidos e confirma o projeto desta conversa. |
| Consultar situação | `/status` | Projeto, modelo, tarefa atual, estado e custo conhecido. |
| Acompanhar equipe | `/atividade` | Agente, tarefa e estado em texto legível, atualizado na mesma mensagem. |
| Consultar tarefas | `/tarefas`, alias de `/tasks` | Kanban do projeto vinculado. |
| Parar trabalho | `/cancelar` e botão | Solicita interrupção da execução identificada e informa o resultado real. |
| Decidir aprovação | Botões `Aprovar uma vez` e `Negar` | Decide somente a operação pendente apresentada. |
| Iniciar autonomia | `/loop <objetivo>` | Cria tarefa governada no projeto vinculado e devolve seu identificador. |
| Reiniciar conversa | `/new` e `/clear` | Histórico novo; não cancela silenciosamente um trabalho em execução. |

Exemplo ilustrativo de uma mensagem de acompanhamento:

```text
Projeto: investimentosOS
Tarefa: revisar os testes de ativos
Estado: em execução

backend — corrigindo as rotas
testes — aguardando backend
revisor — aguardando validação

Custo registrado: US$ 0,42
[Atualizar] [Cancelar tarefa]
```

O texto é uma apresentação dos eventos reais. Não inventar membros, progresso,
percentuais, custo zero quando desconhecido, nem marcar conclusão por silêncio
do modelo ou encerramento do streaming.

## Escopo e decisões

- A seleção de projeto é explícita e persistida por conversa/operador. A seleção
  global da tela Projetos pode ser oferecida como sugestão inicial; não muda
  automaticamente o projeto de um chat ou de uma tarefa já iniciada.
- Cada execução captura projeto, workspace, operador, sessão e limites no
  momento de admissão. Trocar projeto abre contexto separado; histórico,
  ferramentas, mídia e aprovações não atravessam projetos.
- A V1 dos novos controles de execução/aprovação funciona em conversa privada
  com usuário autorizado. Nos grupos, manter os recursos existentes e recusar
  os novos controles privilegiados com orientação para conversar em privado.
  Suporte completo a grupos/tópicos fica para outra entrega.
- Telegram é uma interface do runtime existente, não um segundo orquestrador.
  Extrair serviços compartilháveis do web quando necessário, preservando seus
  endpoints, autenticação e comportamento.
- Preservar o orçamento configurado pelo operador, inclusive o limite diário
  atual de US$ 1.000. Não embutir esse valor como default nem ampliá-lo.
- Não compartilhar automaticamente o histórico web/Telegram. Uma futura
  identidade unificada exige contrato próprio.
- Não liberar `allow_all`, desabilitar safe mode, instalar credenciais em
  arquivos versionados ou criar aprovação permanente pelo Telegram nesta V1.

## Etapa 0 — Contratos e preparação

1. Criar a próxima sprint disponível com `SPEC.md`, `ARCHITECTURE.md` e
   `BACKLOG.md` antes de editar código. Registrar skills, responsabilidades,
   migrações, critérios de aceite e comportamento em falhas.
2. Abrir branch `codex/sprint-XX-telegram-control`; não misturar alterações
   preexistentes na árvore de trabalho.
3. Mapear caminhos native, bridge e Agno realmente usados pelo gateway;
   confirmar propagação de ContextVars, run/session/project e custos nas
   threads. As garantias das etapas seguintes devem valer para todos os
   caminhos suportados, ou o caminho incompatível deve ser recusado claramente.
4. Definir serviços compartilhados para vínculo de projeto, consulta de
   atividade, cancelamento e aprovação; separar decisão de autorização da
   apresentação específica do Telegram.

**Aceite**: contratos documentados, baseline hermético passando e nenhuma
ativação de canal ou chamada a provider real.

## Etapa 1 — Projeto e isolamento da conversa

1. Implementar `/projetos`, `/projeto` e seleção com botões usando o registro
   existente, filtrado pela política de acesso do operador.
2. Persistir vínculo e contexto por operador/chat/projeto, incluindo overrides
   de modelo. Identificadores de callback são opacos, validados no servidor.
3. Resolver router, prompt, memória, Kanban e diretório de mídia a partir do
   mesmo workspace efetivo. Não alterar o CWD global do processo.
4. Mostrar projeto em `/status`, início da tarefa e aprovações. Projeto ausente,
   removido ou inacessível bloqueia nova execução; nunca cair silenciosamente
   em outro workspace.
5. Oferecer o workspace configurado como opção explícita quando não houver
   projeto registrado, com nome claro e confirmação do vínculo.

**Aceite**: dois projetos com arquivos de mesmo nome produzem respostas e ações
no diretório correto; alternar não mistura histórico; restart preserva vínculo;
troca de projeto não redireciona tarefa em andamento.

## Etapa 2 — Cancelamento e limites efetivos

1. Registrar a relação operador/chat/projeto → run e disponibilizar
   `/cancelar`; se houver várias execuções, exigir escolha por identificador.
2. Usar a API de cancelamento do Kernel e um sinal cooperativo propagado às
   threads, chamadas de modelo, espera por retry e fronteiras de ferramentas.
3. Verificar cancelamento, kill-switch e budget antes de novo LLM/tool e antes
   de retomar uma aprovação. Preservar o custo já consumido.
4. Para subprocessos gerenciados, propagar interrupção e encerrar a árvore
   conforme o suporte Linux/Windows. Para operações não interrompíveis, mostrar
   `cancelamento solicitado`, impedir novos passos e relatar o desfecho real.
5. Tratar timeout como interrupção solicitada, preservando sessão e resultado
   parcial sem iniciar uma execução concorrente sobre o contexto antigo.

**Aceite**: cancelar em geração, retry, aprovação e subprocesso não permite
nova ferramenta após o sinal; nenhuma sessão pode cancelar a run de outra;
estado terminal e efeitos parciais são coerentes. Nunca prometer desfazer
efeitos já realizados ou encerrar à força uma thread Python.

## Etapa 3 — Atividade dos agentes

1. Reutilizar RunManager/EventBus e a projeção `build_chat_activity`, extraindo
   um módulo compartilhado se necessário.
2. Implementar `/atividade` e botões de atualização com agente, tarefa,
   ferramenta, estado, motivo de espera e custo conhecido.
3. Atualizar uma mensagem por execução, com coalescência de eventos, limites de
   edição e respeito a `retry_after`. Entregar mudanças relevantes e o resumo
   terminal mesmo se uma edição intermediária falhar.
4. Exibir aprovação, cancelamento solicitado, retry, erro e conclusão sem JSON
   cru, prompts, argumentos sensíveis ou erros internos integrais.

**Aceite**: duas delegações ao mesmo agente aparecem separadas; dados de outra
sessão não aparecem; falha no Telegram não interrompe a tarefa; mensagens longas
mantêm formatação válida e não inundam o chat.

## Etapa 4 — Aprovação remota da operação exata

1. Integrar `ApprovalManager` e os mecanismos de suspensão/continuação que já
   atendem o web. Não reutilizar cegamente um fluxo legado que apenas insere
   consentimento em texto ou amplia a allowlist.
2. Apresentar resumo seguro da operação, projeto, tarefa, motivo e escopo
   `uma vez`, usando `approval_summary` e política de redação existente.
   Quando o resumo não bastar para decidir, disponibilizar detalhes seguros
   autenticados; não aprovar uma operação ambígua.
3. Vincular callback a usuário privado autorizado, chat, mensagem, projeto,
   sessão, run, approval e digest da operação. O callback transporta apenas
   referência opaca; toda autorização é revalidada no servidor.
4. Usar expiração limitada (proposta: 10 minutos), consumo atômico e decisão
   idempotente. Dois cliques, callbacks antigos ou decisões concorrentes no
   web e Telegram não podem duplicar a operação.
5. Retomar apenas a continuação válida; nunca reenviar o objetivo inteiro ao
   modelo para repetir passos anteriores. Negar, expirar, cancelar ou ativar
   kill-switch impede execução e remove os botões acionáveis.
6. Sem continuação segura após restart, manter o pedido bloqueado/expirado e
   informar o operador; não executar novamente por tentativa automática.

**Aceite**: aprovar executa a operação exata uma única vez; negar executa zero
vezes; payload alterado, usuário errado, projeto trocado, replay, expiração e
restart não concedem autorização. HARDLINE continua incondicional.

## Etapa 5 — Trabalho autônomo e recuperação

1. Implementar `/loop` como entrada para o motor governado compartilhado com
   o web, com limites explícitos herdados da configuração e comprovante de
   admissão antes de dizer que a tarefa começou.
2. Persistir a aceitação de updates antes de avançar o offset, com chave única
   por identidade do bot/update e vínculo ao job/run. Proteger contra duas
   instâncias consumindo o mesmo bot; migrar o offset legado sem reprocessar
   automaticamente o histórico.
3. Diferenciar `recebido`, `admitido`, `em execução`, `interrompido` e terminal.
   Recuperar registros pendentes pelo runtime; um resultado incerto de operação
   externa exige reconciliação, nunca replay cego.
4. Usar entrega/outbox existente quando adequado, com retries limitados para
   avisos e relatório final: resultado, validação, custo conhecido, motivo de
   parada e pendências. Não prometer entrega exatamente uma vez da API externa.
5. Persistir conteúdo de jobs apenas quando necessário à retomada, com acesso
   restrito e retenção definida; telemetria guarda somente metadados redigidos.

**Aceite**: restart após receber update ou emitir aprovação não perde o vínculo
nem duplica tarefa/efeito; queda do Telegram não altera budget nem declara
sucesso; relatório terminal reflete os gates reais do Kernel.

## Etapa 6 — Operação na Beelink e validação real

1. Documentar configuração via `bauer gateway init`, token fora do Git,
   allowlist de usuários e comandos atuais `bauer gateway telegram ...`.
2. Instalar/validar o serviço persistente existente do gateway, com reinício,
   logs redigidos e diagnóstico de token ausente, canal desativado, usuários
   vazios e conflito de polling. Não abrir portas públicas para long-polling.
3. Depois dos testes herméticos, configurar o bot com credencial fornecida por
   canal seguro e confirmar o usuário autorizado; não enviar mensagem a
   terceiros. Validar de forma interativa com o próprio operador.
4. Executar jornada controlada em projeto temporário: selecionar projeto,
   conversar, enviar áudio/documento, acompanhar equipe, aprovar, negar,
   cancelar, reiniciar gateway e consultar o resultado.
5. Publicar por PR e aplicar a versão validada na Beelink, conferindo ausência
   de trabalho ativo antes de reiniciar serviços. Registrar commit carregado,
   estado do serviço e evidência real separada da evidência simulada.

**Aceite**: bot responde ao operador autorizado, recusa os demais, mantém
vínculo após restart e apresenta controles testados. Sem token ou jornada
real, registrar implantação/validação real pendente; não marcar o plano DONE.

## Estratégia de testes e gates

Testes novos devem seguir os vizinhos de Telegram/gateway, usar
`httpx.MockTransport`, `tmp_path`, providers fake e relógio controlado. Cobrir
também concorrência de callbacks, falhas de persistência, cancelamento,
ContextVars nas threads, isolamento de projetos e migração do offset.

Executar testes focados em cada etapa e os gates completos antes de cada
entrega de sprint, conforme `AGENTS.md`:

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
uv lock --check
git diff --check
```

Manter os invariantes de custódia do Kernel e a matriz Linux/Windows. Alterações
de config são estritas; atualizar código antes de distribuir novos campos.
Não alterar dependências sem atualizar e versionar `uv.lock`.

## Organização e acompanhamento

- **Skill aplicável**: `spec-driven-project-setup`, em
  `.agents/skills/spec-driven-project-setup/SKILL.md`; seguir também `AGENTS.md`.
- **Papéis recomendados para a implementação**: arquiteto dos contratos;
  implementador de gateway/Telegram; especialista em execução/aprovação;
  revisão independente de testes e autorização. Se usar agentes em paralelo,
  dividir arquivos/contratos e integrar antes de avançar de etapa.
- Dividir em sprints sequenciais: contexto; cancelamento; atividade;
  aprovação; autonomia/recuperação; operação. Cada sprint possui seus próprios
  SPEC/ARCHITECTURE/BACKLOG, branch, revisão e gates; não declarar a próxima
  sprint entregue com pendências obrigatórias da anterior.

Checklist de execução:

- [x] Contratos e baseline (etapa 0).
- [x] Projeto e isolamento (etapa 1).
- [ ] Cancelamento e limites (etapa 2).
- [ ] Atividade dos agentes (etapa 3).
- [ ] Aprovação remota exata (etapa 4).
- [ ] Autonomia e recuperação (etapa 5).
- [ ] Operação e jornada real na Beelink (etapa 6).
- [ ] Revisão, documentação e atualização do status em `plans/README.md`.

## Condições de parada

Parar a etapa afetada e registrar o impedimento se não for possível:

- assegurar custódia do Kernel e preservar budget/kill-switch;
- distinguir execução viva de registro abandonado antes de retomar trabalho;
- ligar consentimento à operação exata sem replay ou ampliação de permissão;
- impedir cruzamento de usuário, projeto, sessão ou mídia;
- comprovar cancelamento nos caminhos que a interface anuncia como canceláveis;
- manter testes herméticos ou preservar compatibilidade Linux/Windows.

Não substituir essas garantias por autoaprovação, status fictício, relaxamento
de gates ou repetição de tarefas. Bloqueio de uma etapa não transforma as
seguintes em concluídas.
