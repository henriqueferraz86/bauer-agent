# BACKLOG — Telegram via AgentOS HTTP

O [plano de ativação do Tool Gateway](PLANO_ATIVACAO_TOOL_GATEWAY.md) detalha os bloqueios restantes, responsáveis e critérios de validação na Beelink.

Referência: plano técnico de 13/11/2026 anexado à solicitação. Status iniciais abaixo não representam validação de ambiente. Trabalhar incrementalmente e não avançar de fase sem cumprir seu gate.

- [ ] **Fase 0 — baseline e contrato:** inspecionar Beelink, versão/commit, configuração sem segredos, saúde, autenticação, catálogo, OpenAPI, rede, discos e backup; teste read-only de agente e time. Gate: autenticação efetiva, catálogo não vazio e contrato da versão comprovado; caso contrário abortar rollout.
- [ ] **Fase 1 — segurança e governança:** autenticação e rede privadas; identidade e escopo confiáveis; inventário de tools; policy/approval server-side ou Tool Gateway; auditoria correlacionada. Gate: bloqueio de ação proibida, uma execução após aprovação e isolamento entre usuários.
- [ ] **Fase 2 — cliente HTTP:** auth, catálogo, operações de agentes/times, JSON/SSE, erros e timeouts, reconciliação e persistência idempotente; sem retry cego de POST. Gate: testes herméticos para contrato, chunks, falhas, timeout, reconnect, cancelamento e duplicação.
- [ ] **Fase 3 — roteamento:** modo explícito Telegram, sessão e geração persistentes, roteamento por catálogo, registry, comandos e estados. Gate: piloto via HTTP com remote_run_id e nenhuma chamada involuntária ao SDK local.
- [ ] **Fase 4 — HITL/cancelamento:** aprovação autenticada de uso único, cancelamento confirmado terminal, ownership, restart e métricas parciais. Gate: cenários de cancelamento, aprovação, time e recuperação sem dupla execução.
- [ ] **Fase 5 — SSE/mídias/tarefas longas:** deltas e sanitização, anexos compatíveis e limites, recuperação durável explícita para trabalho longo. Gate: sem duplicação de edição ou efeito, anexos corretos e teste de restart.
- [ ] **Fase 6 — canary/corte:** manter flag desligada no rollout inicial, CI e revisão de segurança; ao menos 20 turnos read-only; liberar escrita após validação de aprovação; exercitar rollback; só então alterar default Telegram. Gate: auditoria HTTP com remote_run_id, governança e recuperação comprovadas e ausência de execução Native/SDK no caminho normal.
- [ ] **Revisão e entrega:** matriz completa do plano, suíte e lint exigidos por AGENTS.md, runbook, limitações e evidências. Não declarar pronto enquanto houver gate pendente.

## Estado do canário — 2026-11-15

O canário read-only no Telegram está ativo em uma stack isolada na Beelink. A prova de health, perfil assinado, negação de chamada não autenticada, `getMe`, isolamento de credenciais e poller único passou; havia zero updates pendentes no momento da troca. A política de reinício do container Bauer antigo está temporariamente em `no` para impedir que o poller legado reapareça durante a janela de canário. Os gates de 20 turnos, cenários de falha/cancelamento, rollback, HITL de escrita e corte continuam abertos; não habilitar escrita nem mudar o adapter padrão.

## Baseline observado na Beelink — 2026-11-13

AgentOS informa versão 3.2.0, `auth_mode: none` e `user_isolation: false`; porta 7777 exposta em todas as interfaces. Catálogo não vazio. O gate da fase 0 **não passou**: rollout HTTP bloqueado até autenticação efetiva, isolamento de rede e demais verificações do plano. Este registro não contém credenciais.

O checkout remoto está no commit `675aedd51db9f3f79cf58d61abe48abdd9b69b56`. O catálogo contém nove agentes e o time `bauer.software_team`; o OpenAPI apresenta rotas de execução, consulta, cancelamento e continuação, com criação por formulário multipart. Gateway e AgentOS compartilham volumes de workspace, memória e logs. O AgentOS local está em outra versão (Agno 3.0.10), portanto os contratos precisam ser validados contra o serviço instalado.

Ainda faltam inventário completo de consumidores e testes read-only de agente e time. Os testes remotos não foram executados porque a ausência de autenticação e de governança comprovada impede garantir que ferramentas não produzam efeitos. Nenhum container ou configuração foi alterado.

Em 13/11/2026 foi feito backup protegido de configuração, ambiente, modelos e override em `/home/henrique/.local/share/bauer-backups/telegram-agentos-20261113-152225-bf968023`, junto com cópias via SQLite backup de 13 bancos do volume de memória. `PRAGMA quick_check` passou nos 13 arquivos. O backup não é um snapshot transacional conjunto entre bancos; repeti-lo imediatamente antes do rollout. Serviços, configuração efetiva e containers não foram alterados.

## Trabalho local em andamento

- Cliente HTTP, store transacional e executor de submissão foram preparados isoladamente, com testes herméticos. Não estão conectados ao Telegram e não satisfazem ainda os gates de recuperação, HITL, cancelamento e SSE da sprint.
- Uma primeira proteção experimental foi removida após revisão. A fronteira restrita posterior permanece desligada e não pode ser usada em produção: uma revisão independente confirmou que a chave compartilhada não autentica usuários individuais. Consulta e cancelamento estão bloqueados nessa fronteira até haver controle de ownership comprovado.
- O checkout real não tem o registry, roteador Agno Telegram ou seleção por projeto presumidos pelo plano. A fila atual guarda apenas texto e perde identidade/update_id; a integração precisa preservar o envelope completo ou recusar concorrência de modo seguro.
- Baseline local anterior à implementação: os dois comandos Ruff passaram; a suíte completa teve oito falhas em uma árvore com alterações preexistentes, inclusive casos de aprovação e custódia. É necessário repetir a validação após a implementação e investigar essas falhas antes do corte.
- Nova execução da suíte completa terminou com cinco falhas, em modelos offline, aprovação de chat, interface de revisão, imutabilidade de run terminal e catraca de custódia. A suíte já havia falhado antes destas alterações; as causas ainda não foram resolvidas. Os testes focados dos novos módulos e os dois checks Ruff passaram.
- JWT e isolamento entre usuários foram testados localmente no Agno 3.0.10 com a fábrica Bauer; o emissor foi testado com scopes restritos. Na Beelink, a função de autorização do Agno 3.2 foi verificada isoladamente, sem executar runs. A autenticação efetiva e a governança de ferramentas no serviço remoto continuam pendentes.
- Em 13/11/2026, um snapshot de código e catálogo foi testado na Beelink usando a imagem `bauer-agent-agentos` com `--network none`, sem acesso a volumes de produção: 13 testes de autenticação, JWT, boundary e isolamento passaram no Agno 3.2. Não é evidência de que o serviço de produção esteja autenticado nem cobre HITL e ferramentas de escrita.
- O polling atual confirma o offset antes de persistir a execução. Uma caixa de entrada durável foi implementada isoladamente, mas ainda não está conectada ao bridge; é necessário preservar o envelope completo e proteger o texto persistido para não perder mensagens ou suas identidades após reinício.

## Base de integração corrigida

A worktree desta sprint está em `codex/sprint-84-telegram-agentos-http`, criada do commit `675aedd` em execução na Beelink. O checkout original `feb67a1` estava 173 commits atrás; afirmações anteriores sobre ausência de registry, projetos e `_turno_agno` aplicam-se apenas àquele checkout. Nenhuma integração antiga deve ser portada sem adaptação e testes nesta base.

Em 13/11/2026, o config efetivo consultado sem revelar segredos indicou Telegram habilitado, `allow_all=false`, um usuário na allowlist e `telegram_default_adapter=agno`. O adapter global estava em `bauer_native`. A configuração possui token Telegram disponível. Esses valores devem ser revalidados antes do rollout.

## Bloqueio de ferramentas de escrita e Tool Gateway

O perfil HTTP atual remove todas as tools do catálogo e bloqueia `/continue`. Uma prova isolada do Agno mostrou que repetir o mesmo `/continue` pode executar a mesma escrita novamente. O coordenador experimental e seu router não estão registrados no AgentOS: eles demonstram aprovação de um único requisito PAUSED, digest calculado no servidor, claim transacional, rejeição de replay e estado UNKNOWN após falha, mas ainda não integram a policy do Bauer nem o callback Telegram. A confirmação do Agno não substitui aprovação de `ToolRouter`.

O bloqueio concreto é a localização e autoridade de contexto: o Kernel do Gateway usa store e workspace do projeto selecionado; o AgentOS tem `runtime_root` e workspace próprios e pode não montar os projetos. No caminho HTTP, o adapter não recebe o `tool_router` do turno, nem `gateway_run_id`, `gateway_session_id`, event bus e contexto de projeto confiáveis. O form ou o modelo não podem fornecer esses valores como autorização.

Para liberar escrita sem depender de montar projetos no AgentOS:

1. Implementar um Tool Gateway no processo Bauer, na rede privada, que receba chamadas autenticadas por serviço e um vínculo curto e não reutilizável à run do Kernel. Resolver run e projeto nos stores locais do Gateway e conferir `operator`, canal, sessão, estado, projeto e workspace registrado; rejeitar runs terminais, bindings alterados e quaisquer caminhos fornecidos pelo modelo como raiz de workspace.
2. Fazer o AgentOS expor apenas wrappers explícitos e versionados, todos governados e com confirmação, que chamem o Tool Gateway. Vincular `remote_run_id`, `tool_call_id`, identidade e digest canônico de argumentos ao `gateway_run_id` server-side. O servidor deve avaliar **cada** tool no `ToolRouter` real, inclusive após uma continuação ou delegação de time.
3. Integrar `ApprovalManager` e seu registro persistente de eventos com o callback Telegram autenticado, verificando owner/chat/projeto, validade e uso único. Toda continuação deve começar apenas após decisão confirmada, claim transacional e revalidação de PAUSED/digest; cancelamento e claim devem compartilhar a barreira persistente, e estado incerto nunca deve ser executado novamente automaticamente.
4. Provar em processo separado e no AgentOS 3.2: ação proibida negada; escrita ausente antes da aprovação e presente uma vez depois; negação sem efeito; replay e concorrência sem dupla escrita; usuário B não aprova/cancela run de A; tentativa de trocar projeto/workspace/args rejeitada; segunda tool proibida após continuação não executa; crash antes/depois de efeito preserva UNKNOWN; cancelamento durante LLM/tool não é anunciado como terminal até confirmação real.

Não habilitar tools ou registrar o endpoint experimental até essas provas e a revisão de segurança. O teste ativo já demonstrou que o cancelamento do dono pode receber 404 antes da persistência da run; esse cenário continua um gate pendente.

### Progresso do protótipo de Tool Gateway e bloqueio de integração

Na worktree de integração há um autorizador que confere JWT RS256, run ativa, identidade, binding, projeto, workspace e mapeamento remoto no processo Gateway. Um attestor isolado obtém o requirement PAUSED do próprio AgentOS e assina identidade, sessão, run, tool_call_id e argumentos com challenge; o emissor vincula seu digest e o ledger impede repetição mesmo com JWT reemitido. O router experimental só permite tools de leitura, passa pelo ToolRouter real e permanece **não registrado**. Esses módulos são protótipos testáveis, não um Tool Gateway de produção.

Para integrar sem corrida, falta um sequenciador autoritativo no Gateway, compartilhado por **todos** os caminhos de efeito e de cancelamento/terminal: `/cancelar`, kill-switch, timeout, falhas e transições do Kernel. O ledger experimental do Gateway e a barreira do AgentOS hoje são bancos distintos; simplesmente revalidar a run antes da tool deixa uma janela na qual o cancelamento pode ocorrer depois da verificação. O protocolo deve negar ou esperar cancelamento enquanto houver efeito em voo, persistir incerteza após crash e proibir nova execução. Testar concorrência entre processos, inclusive restart e transições terminais, antes de registrar o router.

Falta também expor o attestor apenas em transporte autenticado, vinculá-lo ao JWT do usuário e ao callback Telegram, conectar a decisão single-use ao `ApprovalManager`/ToolRouter real e validar todos os wrappers/delegações, não só a primeira tool. A prova deve incluir duas máquinas/processos, paths/workspace adulterados, segunda tool proibida, replay com novo JWT e resultado incerto. Until those checks and the plan's full gates pass, no write tool or generic `/continue` may be enabled.

A versão atualizada do snapshot de segurança da base 675aedd foi testada na imagem AgentOS 3.2 da Beelink em container isolado (`--network none`, sem volumes de produção): 21 testes passaram e um teste de cancelamento durante execução ativa ficou xfail. A prova não autoriza o corte: o cancelamento ainda pode retornar 404 antes da persistência da run, e Tool Gateway/HITL não estão integrados.

## Validação em 14/11/2026

O registro de runs ativas foi endurecido: mantém o vínculo de identidade após o fim do stream, recusa reutilização de ID e não trata runs terminais como ativas. A bateria focada atual passou com 25 testes no ambiente local; outros 10 testes de barreira e recuperação passaram na rodada combinada. O snapshot atual executou 209 testes com sucesso na imagem AgentOS 3.2 da Beelink, em container sem rede e sem volumes de produção. Essa evidência não cobre um rollout, aprovação de escrita ou serviços reais. A Beelink permanece no commit `675aedd`, sem alteração dos containers.

Pendências bloqueadoras: Tool Gateway e HITL continuam não registrados; os testes de sincronização e recuperação da barreira ainda não provam resistência à restauração de um snapshot antigo do ledger; conteúdo de projeto não é encaminhado; áudio e imagens ainda não têm paridade; o canary e o rollback de produção não foram executados. O OpenAPI da instalação 3.2 expõe campo multipart `files`, mas a semântica multimodal e limites de processamento ainda exigem validação.

A suíte completa da worktree terminou com código 0 em 14/11/2026, após `uv sync --frozen --extra dev`; os dois checks Ruff também passaram. O Tool Gateway segue não registrado: a separação entre ledger e SQLite de runs não resiste a uma restauração antiga do mesmo ledger, e a aprovação de escrita/callback ainda não está integrada. A liveness de runs SSE foi endurecida para evitar cancelar uma run histórica após o encerramento do stream. A porta de produção e o adapter do Telegram não foram alterados.

A restauração de um backup antigo do ledger foi reproduzida em teste isolado: o arquivo mantém o UUID, mas perde o registro de um efeito em voo e deixa a run terminar. O teste `test_restoring_older_ledger_must_not_allow_terminal_during_effect` está marcado xfail estrito para manter o gate visível; nenhuma ferramenta é habilitada até existir armazenamento/barreira que resista a essa inconsistência e passe revisão.

## Barreira em SQLite único — 14/11/2026

O protótipo agora exige que o ledger use o mesmo `runtime_state.sqlite3` da run; o claim verifica run ativa sob o lock de escrita e o guard central reserva a parada na mesma transação. Um backup consistente contém run e claims juntos; um ledger separado é recusado. Na disputa com efeito em voo, a transição terminal é revertida e a intenção de parada é persistida antes de retornar ao chamador. Os testes focados cobrem recovery, processos concorrentes, estado e backup. A restauração integral do banco para um ponto anterior a um efeito externo continua exigindo idempotência do destino ou reconciliação independente.

O router Tool Gateway segue desconectado: ainda faltam política de escrita/ApprovalManager e callback Telegram E2E, cobertura completa de cancelamento/kill-switch/timeout e revisão independente. Não ativar produção apenas com a barreira unificada.

Validação do SQLite único: 58 testes focados passaram no Windows e 212 testes passaram na imagem AgentOS 3.2 da Beelink em container sem rede ou volumes de produção. Os dois checks Ruff passaram. O Tool Gateway segue sem registro e sem autorização para escrita; a unificação da barreira não prova HITL end-to-end.

O guard foi refinado para reverter as alterações da transação a um savepoint e gravar a intenção de parada sem liberar o lock SQLite. Um teste concorrente prova que outro claim espera a gravação da parada e depois é recusado. A bateria atualizada passou com 213 testes na imagem 3.2 isolada; a restauração integral para antes de um efeito externo continua exigindo idempotência do destino ou reconciliação independente.

A verificação de identidade do ledger passou a ocorrer sob o próprio lock de escrita antes de claim, stop ou settle. Na imagem AgentOS 3.2 isolada, a bateria final passou com 213 testes; 59 testes focados passaram no Windows. O router de ferramentas permanece desligado até os gates de autorização, aprovação e execução E2E serem satisfeitos.

A suíte completa `uv run pytest tests/ -q --tb=short` foi repetida após a mudança de identidade e terminou com código 0. Os dois checks Ruff passaram. A bateria da imagem AgentOS 3.2 isolada concluiu 213 testes. Esses resultados não substituem os testes E2E do router de ferramentas e de aprovação, que continua desconectado.

## Registro opcional do Tool Gateway — 15/11/2026

O servidor Bauer registra o router somente quando explicitamente habilitado e com Kernel, chaves, stores e política válidos. O endpoint exige autenticação de serviço e token vinculado à execução, rejeita transporte HTTP fora de loopback e permite apenas leitura. Testes em socket real e na fábrica real do servidor verificam autenticação, isolamento, política, replay e recusa de escrita. O snapshot passou 79 testes na imagem AgentOS 3.2 isolada, sem rede nem volumes de produção.

Uma prova adicional no Agno local e na imagem 3.2 demonstrou que uma ferramenta de execução externa pausa sem executar o callable e aceita resultado externo apenas na sessão do usuário. Isso ainda não conecta AgentOS ao Tool Gateway: faltam atestação da chamada externa, emissão de credenciais vinculadas à run, execução e retomada controladas, e a integração de aprovação com Telegram para escrita. O `/continue` genérico continua bloqueado. Não habilitar a configuração em produção antes dos gates do plano e dos testes E2E reais.

O perfil experimental de leitura agora conecta o executor do Telegram ao AgentOS e ao Tool Gateway, com prova da chamada persistida, token vinculado à run, política, orçamento e retomada autenticada de uso único. Um teste usa dois servidores HTTP reais com o Agno e cobre leitura e orçamento; a bateria também foi executada em container isolado na imagem 3.2 da Beelink. A execução de escrita e o callback de aprovação ainda não estão integrados; a Beelink não recebeu rollout e o gate de produção do plano continua aberto. O `/continue` genérico segue bloqueado.

Validação adicional: 65 testes passaram na imagem AgentOS 3.2 isolada, inclusive o caminho HTTP com limite de tools; a suíte completa no Windows e os dois checks Ruff terminaram com sucesso. O teste isolado cobre cancelamento anterior à retomada e resposta perdida sem repetição. Uma consulta somente de leitura ao serviço real confirmou `auth_mode=none` e `user_isolation=false`; isso não é autorização para alterar produção.

## Validação final da sprint — 2026-11-15

`uv sync --frozen --extra dev`, a suíte completa `uv run pytest tests/ -q --tb=short` e os dois checks Ruff exigidos passaram na worktree `codex/sprint-84-telegram-agentos-http`. A suíte foi repetida após corrigir a corrida de cancelamento: o Bauer registra a parada durante uma ferramenta em andamento, não envia cancelamento remoto até a ferramenta terminar e então interrompe a continuação AgentOS. Os testes focados e `git diff --check` também passaram.

A entrega em `master` pode atualizar o servidor Bauer com uma imagem identificada pelo commit, sem habilitar escrita. O canário Telegram permanece isolado, restrito ao projeto sintético e read-only. Continuam pendentes: a resposta real do operador no Telegram, 20 turnos observados, cenários completos de falha e rollback, callback de aprovação de escrita, segurança do AgentOS legado e qualquer troca do adapter padrão. Não contar healthcheck, `getMe` ou testes sintéticos como turnos do canário.
