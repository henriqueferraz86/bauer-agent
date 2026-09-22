# Plano 057 — Expandir com segurança as capacidades Agno no Bauer

**Status:** TODO

**Prioridade:** P1

**Esforço total estimado:** XXL, dividido em sprints independentes

**Dependência inicial:** Sprint 17 de observabilidade (#151) integrada e verde
**Referências:** levantamento arquitetural e avaliação do catálogo no
[PR #151](https://github.com/henriqueferraz86/bauer-agent/pull/151), e
[documentação oficial do Agno](https://docs.agno.com/llms.txt).

## Problema e resultado esperado

O Agno oferece centenas de integrações, mas o adapter Bauer expõe apenas um
conjunto pequeno de ferramentas próprias. A existência de um toolkit no SDK não
faz com que ele passe pelas políticas, permissões, isolamento, aprovações e
auditoria do Bauer. Conectar tudo diretamente aos times criaria permissões
difusas, dependências obrigatórias e risco de efeitos externos inesperados.

Este plano cria uma camada configurável para tornar **todo o catálogo compatível
do Agno descobrível e habilitável dentro do Bauer**. A ambição é suportar o
catálogo inteiro, não parar nos primeiros conectores. Cada integração pode ser
opcional por dependência/credencial, e cada agente recebe somente as ferramentas
que foram habilitadas para ele. A entrega é incremental para que a cobertura
avance sem entregar permissões globais de uma vez. O Kernel e o ToolRouter
continuam governando chamadas; escritas e ações externas têm aprovação explícita.

## Escopo

- Reutilizar as tools Bauer e suas políticas antes de adicionar implementações
  paralelas do Agno.
- Declarar por agente os toolkits permitidos, ações, dependências opcionais e
  credenciais necessárias.
- Manter um catálogo extensível que represente todas as integrações Agno
  compatíveis com a versão instalada e mostre claramente o estado de cada uma:
  pronta, requer configuração, dependência ausente, bloqueada por policy ou
  incompatível.
- Integrar em ordem: tools Bauer existentes; GitHub/MCP/web em leitura; análise
  de dados; browser/testes; inventário de infraestrutura; workflows e evals;
  por fim, um piloto de integração com efeitos externos.
- Expor no desktop quais capacidades estão habilitadas, quais dependências ou
  credenciais faltam e quais chamadas foram aprovadas/negadas, sem exibir
  segredos, prompts, argumentos ou resultados privados.
- Preservar configuração, providers e funcionalidades existentes durante
  instalação, atualização e desativação de qualquer integração.

## Fora de escopo

- Substituir o Bauer Kernel, policy engine, EventBus, servidor ou Desktop pelo
  AgentOS.
- Instalar automaticamente dependências, servidores MCP ou credenciais.
- Conceder todas as tools a todos os agentes automaticamente. O catálogo inteiro
  deverá poder ser habilitado/configurado; a permissão continua individual.
- Gravar conteúdo de prompts, argumentos/resultados de ferramentas ou tokens na
  telemetria.
- Permitir escrita em GitHub, banco, cloud ou canais externos sem aprovação.
- Migrar as memórias existentes para o armazenamento Agno antes de uma decisão
  arquitetural separada.

## Invariantes de segurança e compatibilidade

1. **Default deny:** cada agente recebe uma lista positiva de ferramentas e
   ações. Uma integração ausente, inválida ou não configurada não ganha acesso.
2. **Governança única:** chamadas com efeito passam pelo Kernel, policy,
   orçamento e aprovação Bauer; nenhum adapter declara um run concluído fora da
   custódia do Kernel.
3. **Read-only como ponto inicial:** GitHub, MCP, SQL, Docker e AWS/EKS começam
   com operações de leitura explicitamente selecionadas. Um toolkit Agno não é
   considerado seguro só por estar instalado.
4. **Instalação não mutante:** `bauer update` e setup não sobrescrevem config
   funcional, provider, modelo, voz/TTS, allowlists, credenciais nem dados do
   usuário. Campos novos têm defaults compatíveis; migrações são atômicas,
   preservam comentários quando possível, geram backup e nunca removem campos
   desconhecidos silenciosamente.
5. **Dependências opcionais:** Bauer inicia se toolkit, pacote ou credencial não
   estiver instalado; `bauer doctor` explica o estado e como habilitar.
6. **Telemetria mínima:** registrar agente, ferramenta, ação, run, duração,
   estado, política e ID da aprovação; redigir valores e conteúdo.
7. **Escopo isolado:** filesystem, browser, SQL, MCP e execução de dados usam
   workspace/identidade/conexão configurados e não ampliam acesso por fallback.
8. **Sem efeitos colaterais implícitos:** nunca enviar mensagens, publicar,
   alterar dados, criar recursos cloud, transferir fundos ou executar comandos
   de escrita só porque um modelo pediu.

## Sequência de sprints

Cada sprint começa com `specs/sprint-XX-*/SPEC.md`, `ARCHITECTURE.md` e
`BACKLOG.md`, numa branch `codex/sprint-XX-*`. A sprint só avança após suite,
lint, typecheck aplicável e gates do Kernel passarem. A ordem abaixo é
sequencial onde há dependência; tarefas sem dependências podem ser planejadas
em paralelo, mas não misturar mudanças de governança em uma entrega sem revisão.

### Sprint 18 — Catálogo Agno completo, capacidades e ponte para o ToolRouter

**Esforço:** L. **Dependência:** #151 integrado; APIs de observabilidade
disponíveis.

- Criar registro tipado de capacidades Agno com identificador estável, ações,
  nível de risco, dependências opcionais, escopo e origem de credenciais.
- Criar índice descobrível para o catálogo completo compatível com a versão
  instalada do SDK. Cada toolkit aparece mesmo sem pacote ou credencial,
  mostrando requisitos e estado; ausência de dependência não oculta a opção.
- Suportar descoberta por metadados/fábricas conhecidas e um mecanismo de
  registro de adapters, sem executar importações de conectores ou código de
  terceiros só para montar a lista do catálogo.
- Declarar explicitamente riscos e ações de cada integração antes de ativá-la;
  integração ainda não classificada aparece como disponível para revisão, mas
  permanece desabilitada até receber policy explícita.
- Declarar capacidades por agente/time; validar configuração estrita e rejeitar
  nomes/ações desconhecidos em vez de ignorá-los.
- Adaptar as tools já disponíveis no Bauer (workspace, web fetch/search,
  browser, memória) para os contratos do SDK sem duplicar execução ou policy.
- Submeter cada invocação ao ToolRouter e ao contexto do run; classificar ação
  antes de encaminhar e emitir eventos redigidos de início/fim/negação.
- Mostrar estado da capacidade no `doctor` e no painel: ativa, não configurada,
  dependência ausente ou negada por policy.
- Garantir que config antiga continue válida e que configurações locais não
  sejam substituídas por defaults durante atualização.

**Aceite:** catálogo lista todas as integrações compatíveis da versão Agno
instalada e indica o estado/requisitos de cada uma; integração não classificada
permanece visível, mas inativa; dois agentes com configurações diferentes
recebem somente suas próprias tools; chamadas negadas não executam; telemetria
não contém argumentos/resultados/segredos; falta de pacote opcional não impede
boot; testes de upgrade provam preservação da configuração existente.

**STOP:** parar se uma tool puder chamar diretamente fora do ToolRouter, se
escopo de workspace/usuário for perdido ou se uma migração alterar configuração
existente sem backup/rollback.

### Sprint 19 — Pesquisa e desenvolvimento em modo de leitura

**Esforço:** M. **Dependência:** sprint 18.

- Integrar GitHub com allowlist positiva de operações read-only (por exemplo,
  leitura de repositório, arquivos, commits e issues); separar permissões de
  escrita em capabilities distintas e inicialmente indisponíveis.
- Integrar busca e extração web reutilizando serviços Bauer, respeitando
  `url_safety`, limites de rede e proteção SSRF.
- Adicionar MCP client para servidores configurados pelo administrador, com
  transporte e endereço permitidos explicitamente, ferramentas enumeradas na
  configuração e autorização por agente.
- Não procurar, instalar nem confiar em MCP servers automaticamente. Tratar
  descrições e respostas de ferramentas como conteúdo não confiável.
- Configurar credenciais pelo cofre existente (`bauer auth`/`credential`), sem
  gravá-las em YAML, logs ou UI.

**Aceite:** Bauer Architect/Research podem ler apenas os repositórios e fontes
autorizados; nenhuma operação de mutação fica disponível; servidor MCP não
configurado não é iniciado; tentativas fora da allowlist são negadas e
auditáveis; timeouts e respostas malformadas não derrubam o run.

**STOP:** parar diante de acesso a repositório/org fora do escopo, execução de
tool descoberta sem aprovação de configuração ou credencial em telemetria.

### Sprint 20 — Bauer Data: análise local e SQL somente leitura

**Esforço:** M-L. **Dependência:** sprint 18; reutiliza política da sprint 19.

- Disponibilizar leitura de CSV/JSON e análise Pandas como dependências
  opcionais, limitada ao workspace autorizado e a budgets de linhas, memória e
  tempo.
- Permitir visualizações/relatórios como artefatos locais com caminhos validados
  e sem upload implícito.
- Adicionar SQL através de conexões nomeadas por administrador; começar com
  usuário de banco read-only e transação/query-only quando suportado.
- Aplicar timeout, limite de linhas, bloqueio de múltiplas instruções e
  restrições de tabelas/esquemas; registrar query apenas redigida ou fingerprint.
- Não usar credencial de escrita para simular read-only quando o backend
  oferece credencial dedicada.

**Aceite:** consultas de leitura válidas funcionam; escrita/DDL, consultas fora
do escopo, resultados grandes e execução que exceda budget são bloqueados;
instalação ausente desativa só a capability; artefatos ficam no workspace
isolado e não alteram arquivos de origem.

**STOP:** parar se o driver não garantir modo read-only, isolamento de conexão
ou cancelamento de consulta por timeout.

### Sprint 21 — Browser e testes de interface

**Esforço:** M. **Dependência:** sprint 18.

- Mapear Playwright/browser do Bauer para o Agno onde possível, mantendo uma
  única sessão governada, política de navegação e limites de download.
- Comparar o toolkit browser Agno com a implementação existente; adotar um só
  backend para evitar sessões concorrentes e credenciais duplicadas.
- Disponibilizar navegação, captura e interação de teste com domínios permitidos
  por perfil; acesso a páginas autenticadas exige perfil explicitamente
  configurado.
- Usar WebdriverIO apenas se houver necessidade concreta não atendida por
  Playwright; não instalar ambos como padrão.
- Mostrar agente, run, página/domínio e estado no painel sem registrar DOM,
  screenshot ou conteúdo privado automaticamente.

**Aceite:** execução em página permitida passa; domínio não permitido, download
arriscado e tentativa de sair do workspace/perfil são bloqueados; cancelamento
fecha sessão e browser; logs não guardam dados de página.

### Sprint 22 — DevOps: Docker e AWS/EKS em leitura

**Esforço:** L. **Dependência:** sprint 18; inventário e identidade operacional
definidos por instalação.

- Expor primeiro status, inventário, logs redigidos e leitura de configuração
  para contextos explicitamente selecionados.
- Restringir clusters, namespaces, contas e regiões por allowlist configurada;
  rejeitar contexto implícito/default ambíguo.
- Usar credenciais de menor privilégio e SDKs opcionais; ausência de credencial
  significa capability indisponível.
- Separar em catálogo as ações de leitura e de mutação. Não expor `apply`,
  `delete`, `exec`, deploy ou alterações de IAM nesta sprint.

**Aceite:** inventário só acessa contextos configurados; tentativas de escrita
ou `exec` são negadas antes da rede; saída sensível é redigida; timeouts e
limites de resposta são aplicados.

**STOP:** parar se o toolkit não permitir remover ou bloquear ações mutantes com
garantia testável, ou se usar credencial mais ampla que o escopo aprovado.

### Sprint 23 — Workflows Agno governados pelo Kernel

**Esforço:** L. **Dependência:** #151 e sprint 18.

- Selecionar um processo real e limitado que se beneficie de etapas, branching,
  paralelismo ou revisão humana; não converter o orquestrador inteiro por
  antecipação.
- Adaptar estados/eventos do workflow ao ciclo de vida de run do Bauer. Kernel
  mantém admissão, custódia, orçamento, policy, retries autorizados e decisão
  terminal.
- Propagar cancelamento, deadlines, contexto de aprovação e identidade entre
  etapas; evitar que o workflow declare conclusão externa ao Kernel.
- Comparar comportamento e custo com o fluxo atual antes de escolher Agno
  workflow para novos casos.

**Aceite:** cenário escolhido passa por sucesso, falha, cancelamento, timeout,
rejeição de aprovação e retomada; todo caminho terminal passa pelos gates do
Kernel; eventos permitem reconstruir a sequência sem armazenar conteúdo
privado.

**STOP:** não migrar caso se a integração depender de `admit()` sem custódia ou
se duplicar scheduler/persistência que o Bauer já possui.

### Sprint 24 — Evals e memória de decisões

**Esforço:** M. **Dependência:** sprint 18; testes de regressão do workflow
quando aplicável.

- Avaliar as métricas/evals do Agno em cenários específicos de seleção de
  agente, uso de tool, delegação e resposta final; converter os resultados
  úteis em casos do harness Bauer.
- Comparar qualidade, custo, tempo e falsos positivos com o harness atual;
  evals Agno não substituem gates obrigatórios do Kernel.
- Registrar decisões estruturadas (alternativas selecionadas, agente/tool,
  resultado e feedback) pela memória de decisão existente, com versão, escopo,
  validade e mecanismo de exclusão.
- Não persistir chain-of-thought. Preferir razões concisas e evidências
  observáveis. Escrita automática de memória deve ser opt-in e sujeita às
  políticas de retenção/privacidade existentes.
- Não migrar para o database Agno nesta sprint; propor ADR separado caso uma
  necessidade mensurada demonstre que os stores Bauer não atendem.

**Aceite:** testes medem regressão e uso correto de tools; nenhum dado sensível
ou raciocínio privado é capturado por engano; memória pode ser inspecionada,
apagada e isolada por escopo; qualidade do harness não cai no baseline.

### Sprint 25 — Primeiro conector com escrita e aprovação

**Esforço:** L. **Dependência:** sprints 18 e 19; fluxo de approval desktop/API
validado.

- Escolher apenas um conector de escrita com caso de uso real (por exemplo,
  GitHub issue ou tarefa em Linear) e uma ação de baixo impacto.
- Modelar operação read e write como capabilities distintas; apresentar alvo,
  resumo do efeito e identidade antes de pedir aprovação.
- Executar somente após aprovação vinculada ao run, ator, argumentos
  autorizados e expiração; rejeição, alteração dos parâmetros ou expiração
  invalida a aprovação.
- Registrar evento de solicitação, aprovação/negação, execução e resultado com
  dados redigidos; adicionar idempotência quando o serviço remoto permitir.
- Iniciar desabilitado; usuário/admin precisa configurar conexão e conceder
  scope mínimo.

**Aceite:** sem aprovação nenhuma escrita remota ocorre; aprovação não pode ser
reutilizada para outro alvo/ação; retries não duplicam efeito; falhas externas
são explícitas e não viram `run.completed` falso.

**STOP:** não adicionar outro conector ou ampliar permissões até concluir revisão
de segurança e validar o primeiro fluxo em ambiente de teste.

### Sprint 26 — Cobertura do catálogo e paridade de integrações

**Esforço:** XL. **Dependência:** sprints 18–25.

- Auditar cada integração publicada no catálogo Agno para a versão travada no
  `uv.lock`; gerar uma matriz de cobertura reproduzível por categoria.
- Para cada toolkit, oferecer um caminho para selecioná-lo por agente: adapter
  Bauer, fábrica Agno suportada, ou declaração explícita de incompatibilidade
  com motivo e dependência faltante.
- Preencher os adapters e manifestos restantes por famílias de capacidades:
  busca/conteúdo, sistemas de código, dados/vector stores, arquivos/documentos,
  mídia/voz, mensageria e serviços cloud.
- Criar testes de contrato reutilizáveis para construção lazy, schema de
  funções, credenciais ausentes, allowlist, classificação de risco, redaction,
  timeout e erro de dependência para cada família.
- Disponibilizar busca, filtros e configuração no Desktop/CLI para navegar no
  catálogo amplo sem carregar os pacotes de todos os conectores no processo do
  servidor.
- Manter inventário de integrações removidas/alteradas upstream; atualização da
  versão Agno não habilita automaticamente novas ferramentas nem muda
  permissões existentes.

**Aceite:** nenhuma integração do catálogo da versão fixada fica sem estado e
justificativa; todas as integrações suportadas podem ser configuradas por
agente sem editar Python; conectores incompatíveis são nomeados e explicados;
extras opcionais são carregados sob demanda; bump do Agno produz diff de
catálogo revisável, sem ampliar permissões automaticamente.

**STOP:** parar atualização do catálogo se uma mudança upstream alterar schema,
permissões, efeitos colaterais ou credenciais sem revisão e teste de contrato.

## Rollout, reversão e compatibilidade

- Todas as capabilities novas começam desabilitadas ou em modo somente leitura.
- Ativação por agente e configuração; nenhuma atualização troca provider,
  modelo, TTS, voice, allowlist já funcional ou credencial.
- `bauer doctor` explica configuração inválida, dependência ausente e capability
  desabilitada sem tentar reparar/instalar automaticamente.
- Cada sprint inclui teste de upgrade a partir de config antiga, migração
  idempotente, backup e rollback. O caminho de desativação não apaga dados nem
  configuração do usuário.
- Telemetria e painel exibem somente metadados de execução; dados detalhados
  ficam acessíveis pelo destino original, com controles próprios.
- Piloto: habilitar primeiro em um agente e workspace de teste; ampliar após
  métricas e revisão de incidentes.

## Métricas de sucesso

- Cobertura: operações úteis Bauer expostas por contrato Agno sem bypass de
  policy; ferramentas e ações visíveis por agente.
- Segurança: 100% das operações mutantes negadas sem aprovação; zero segredo,
  argumento ou resultado privado em eventos/UI; nenhum acesso fora do escopo.
- Confiabilidade: dependência/credencial opcional ausente não impede boot;
  cancelamento e timeout não deixam runs ou agentes falsamente ativos.
- Compatibilidade: atualização preserva configurações e capacidades já
  funcionais; rollback restaura a configuração anterior sem perder dados.
- Qualidade: harness não regrede; cada capability tem testes de sucesso,
  negação, falha, timeout e redaction.
- Adoção: medir chamadas, sucesso, latência, aprovações e falhas por capability
  antes de ampliar o catálogo.

## Validação obrigatória por sprint

Seguir o `AGENTS.md`: `uv sync --frozen --extra dev`, suíte completa,
Ruff bloqueante e de estilo, mypy bloqueante e build do Desktop se houver
mudança de interface. Instalar extras Agno apenas no ambiente de teste que
precisa validar integração real; manter a suíte padrão hermética e sem
credenciais/providers externos. Cobrir Windows quando tocar em paths, processos,
browser ou keychain. Fazer teste de integração com sandbox/mock controlado antes
de habilitar qualquer serviço real.

## Dependências externas e decisões adiadas

- O usuário fornecerá a API key do Jev posteriormente; este plano não depende
  dela e não deve bloquear as capabilities Agno.
- A escolha exata de toolkits e conectores precisa respeitar o catálogo e as
  versões efetivamente suportadas pelo Agno instalado no `uv.lock`.
- A meta de cobertura é o catálogo publicado compatível com a versão Agno
  suportada pelo Bauer. Integrações que dependam de serviços/plataformas
  indisponíveis continuam visíveis com requisitos explícitos, não são
  silenciosamente omitidas nem simuladas como prontas.
- Qualquer uso do AgentOS Control Plane, tracing completo ou database Agno exige
  requisito operacional mensurado e ADR com privacidade, retenção, autenticação,
  migração e coexistência com o Kernel.
