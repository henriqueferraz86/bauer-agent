# Plano de ativação do Tool Gateway na Beelink

Este documento detalha a execução da sprint 84 e os gates do plano de 13/11/2026. O aceite é um turno real no Telegram que passa pelo Kernel, AgentOS e Tool Gateway, com evidência de autorização, retomada, auditoria e rollback. A leitura controlada já foi exercitada com HTTP real e modelo determinístico em ambiente isolado.

| Etapa | Responsável | Dependências | Estado |
| --- | --- | --- | --- |
| 1. Fechar a fronteira de leitura | Segurança e runtime | Integração HTTP existente | Perfil restrito testado. Liberação de projetos reais pendente |
| 2. Preparar implantação e rollback | Infraestrutura | Etapa 1 para ativação | Backup protegido e candidato isolado ativos; ensaio de rollback ainda pendente |
| 3. Executar o canário de leitura | Infraestrutura e testes | Etapas 1 e 2 | Canário real no Telegram ativo em 15/11; aguarda turnos e cenários do gate |
| 4. Integrar escrita com aprovação | Kernel, Telegram e segurança | Contrato da etapa 1 | Pendente |
| 5. Validar paridade e fazer o corte | Integração e testes | Etapas 3 e 4 | Pendente |

## 1. Fechar a fronteira de leitura

- Começar somente com `read_file` nativo no perfil externo. A busca recursiva precisa validar os descendentes e impor limites de trabalho. A listagem precisa evitar seguir links ao consultar metadados. Adicionar essas ferramentas somente após testes específicos de confinamento.
- Impedir substituições por plugins e hooks de ferramentas no perfil restrito. A política precisa rejeitar `ask` sem abrir aprovação por este caminho, corrigir a diferença entre recusa conhecida e resultado incerto e preservar a proteção contra replay nos dois casos.
- Usar somente projeto dedicado com dados de teste no canário. Antes de oferecer leitura a projetos reais, validar política explícita de caminhos exportáveis, arquivos regulares, links, nomes sensíveis e ausência de conteúdo de arquivo nos logs de auditoria. O scanner é defesa adicional.
- Restringir tráfego que carrega credenciais a HTTPS verificado ou loopback literal, sem confiar no proxy de ambiente. Testar JWT de outro usuário, projeto alterado, token expirado, digest alterado, replay, resposta perdida, cancelamento, orçamento, negação por política e retorno efetivo do arquivo ao modelo.
- Renovar a credencial de identidade por chamada após pausas demoradas, sem repetir POST. Exigir a versão assinada do contrato de retomada com digest dos argumentos antes da submissão. Testar também a falha antes de `RunStarted`, sem ID remoto para reconciliação, e definir busca autenticada por ID de requisição ou procedimento operacional sem reenvio.
- Para avançar, demonstrar que o perfil limita o acesso ao projeto de teste, impede escrita e bloqueia a retomada após resposta incerta. Rodar suíte congelada, Ruff e bateria na versão Agno efetiva. Revisar o diff antes de ativar.

## 2. Preparar a implantação na Beelink

- A instalação observada usa Agno 3.2.0 e `auth_mode=none`, `user_isolation=false`. A porta 7777 está publicada na LAN e serve a UI existente. Confirmar novamente o commit, a configuração efetiva, os containers, a exposição e a coleta de logs antes de aplicar qualquer mudança.
- Preparar uma instância AgentOS dedicada ao canal, com JWT, estado e workspace próprios e sem porta publicada na LAN. Montar somente o catálogo/configuração sanitizados e o estado necessário da própria instância. Usar TLS com CA confiável na rede privada do Compose ou usar loopback no mesmo namespace de rede. A UI existente usa rotas que o perfil restrito bloqueia, portanto expor a UI por uma fronteira de acesso separada e testar login, chat e sessões antes de mudar sua configuração.
- Fornecer segredos por serviço. A chave RSA privada, credencial do bot e chave de serviço do Tool Gateway ficam no Bauer. O AgentOS do canal recebe somente as credenciais de identidade, atestação, retomada e o acesso necessário ao modelo. Não compartilhar o `.env`, projetos, socket Docker ou SQLite do Kernel com essa instância.
- Inicializar o store de runs pelo código antes da primeira admissão. Validar o projeto, associação do operador, store de sessões e mesmo SQLite de runtime para servidor e processo do Gateway. Guardar a imagem e os arquivos atuais, e refazer backups protegidos e consistentes imediatamente antes do rollout. Verificar `PRAGMA quick_check` e ensaiar a restauração com serviços isolados.
- O `start.sh` atual inicia o poller antes do `bauer serve`. No candidato de canário, condicionar a admissão à saúde dos dois serviços e à comprovação do perfil assinado. Verificar que as portas e o processo do poller usam o namespace previsto. Fixar a revisão e o digest testados, pois a versão Agno instalada pela imagem pode diferir do lock local.
- Para avançar, verificar saúde, versão, compatibilidade das rotas e isolamento entre dois usuários de teste. Confirmar que o serviço de canal está inacessível pela LAN. O backup e o caminho de rollback devem estar disponíveis antes de recriar containers.

## 3. Executar canário de leitura

- Usar bot e estado dedicados ou roteamento controlado por identidade no único poller. Nunca iniciar dois pollers com o mesmo token. Registrar agente do Telegram com ID próprio e projeto sintético. Habilitar as flags somente no ambiente que atende o canário.
- Realizar pelo menos 20 turnos, incluindo leitura com marcador de teste, falha previsível, usuário não autorizado, sessão nova, queda de conexão e cancelamento. Correlacionar `gateway_run_id`, `remote_run_id`, `tool_call_id`, usuário pseudônimo e estado final. Verificar resposta no chat correto, latência, recuperação, telemetria e custo conhecido ou desconhecido.
- Ensaiar rollback durante o canário. Suspender admissões HTTP, reconciliar as runs, restaurar o adapter anterior, confirmar saúde e testar duas conversas. Preservar os registros necessários para reconhecer escritas, retomadas e entregas incertas.
- Para concluir o canário, documentar 20 turnos, rollback sem duplicação e serviço existente saudável. Leitura em canário é uma etapa anterior à habilitação de escrita.

## 4. Integrar escrita e aprovação pelo Telegram

- Gerar a solicitação de aprovação no processo do Telegram, com o `EventBus`, `ExecutionControl` e Kernel da run. Derivar ação e argumentos do requisito persistido e atestado. Registrar a espera antes de publicar e mostrar operação e destino no botão sem incluir conteúdo ou segredos.
- Usar a validação existente do callback para chat privado, remetente, mensagem, token, expiração, projeto, run e digest. Uma negativa ou revogação deve impedir a emissão ou uso da capacidade. Exigir aprovação para escrita mesmo quando a política básica permitir.
- Consumir aprovação, transferir a run de `queued` para `running` e reservar o efeito na mesma transação do SQLite sob a barreira de cancelamento. Conferir política, kill-switch, projeto, aprovação e o vínculo do `tool_call_id` imediatamente antes da reserva. O primeiro escopo é criação exclusiva de arquivo em diretório existente, sem overwrite, shell ou integrações. A forma atual de `write_file` verifica existência e escreve em etapas distintas, então a implementação precisa de criação exclusiva atômica e evidência independente para reconciliar efeitos após queda.
- Retomar somente com resultado autenticado e vinculado à mesma chamada. Separar a decisão humana da chave de idempotência da continuação. Compatibilizar a persistência com queda de processo, negativa, expiração, resposta perdida e restauração anterior a um efeito. Uma operação incerta exige reconciliação antes de reconsiderar o efeito.
- O teste de integração com callback Telegram e endpoint real deve provar zero efeitos antes da decisão e exatamente um depois. Cobrir negativa, troca de usuário/chat/projeto, expiração, replay, duas aprovações concorrentes, corrida com cancelamento, kill-switch e falha entre efeito e resposta.

## 5. Validar paridade e fazer o corte

- Fechar os critérios restantes do plano original, incluindo agente de entrada, roteamento explicitamente aprovado, streaming, mídias, tarefas longas, custo e recuperação. Registrar regressões de Telegram, CLI e UI e executar os checks do repositório.
- Preparar PR com diff revisado e resultados, e publicar uma imagem identificável pelo commit. Atualizar os containers preservando overrides e volumes. Alterar somente o modo do Telegram após canário, autenticação, aprovação e rollback comprovados. Revalidar `/health`, capacidades assinadas, auditoria do Kernel e fumaça do Telegram.
- No aceite final, informar versão, modo, resultado do teste e instrução para o operador confirmar no Telegram. O trabalho permanece aberto enquanto faltar qualquer gate de corte relevante do plano original.

## Evidências reunidas

- Segurança e runtime implementaram o `read_file` nativo, bloquearam extensões e hooks, exigiram auditoria sem conteúdo e separaram recusa conhecida de efeito incerto. `ask` e `deny` não criam aprovação nessa rota. A retomada autentica o resultado e o digest da chamada persistida e o cliente verifica a versão antes de enviar o turno. A prova HTTP valida o conteúdo recebido pelo modelo determinístico.
- Infraestrutura adicionou e executou o Compose de staging na Beelink. Na imagem de ensaio `sha256:fda39006d69ae340c9270e646caa4d4ff45868aec4dce3c68912380e5f89c36b`, o Compose validou a topologia e a inicialização, JWT, isolamento, run offline e bloqueio de `/continue` no AgentOS. O teste com HTTP real cobriu o Tool Gateway e a retomada. [Compose](../../deploy/tool-gateway-staging.compose.yml) · [Runbook](../../docs/runbooks/tool-gateway-staging.md)
- Os 13 bancos do backup existente passaram em `PRAGMA quick_check` após cópia para diretório temporário protegido na Beelink. Refazer backup antes do corte e testar o rollback completo com o candidato de canário.
- A imagem de ensaio passou em 145 testes focados na Beelink. A verificação adicional confirmou a recusa de leitura por link para arquivo sensível. A suíte completa passou no Windows após corrigir a regressão no registro de ferramentas negadas. Os dois checks Ruff exigidos passaram.
- A Beelink mantém um canário offline separado em `~/.local/share/bauer-tool-gateway-canary`. O servidor Bauer registra a rota com a flag ligada e o AgentOS roda com JWT e isolamento. Três chamadas de leitura concluíram no ledger, incluindo duas após recriação dos containers com os volumes preservados. O SQLite retornou `ok` no `quick_check`. A entrada do Telegram e o modelo nesse ensaio são sintéticos. [Runbook do canário](../../docs/runbooks/tool-gateway-staging.md)
- O canário de leitura no bot foi ativado em 15/11/2026; os 20 turnos, os cenários de falha/cancelamento e o rollback seguem pendentes. A aprovação de escrita e o corte continuam bloqueados. O AgentOS legado na porta 7777 e a UI não foram modificados.

## Atualização operacional — 2026-11-15

- Preparada uma stack isolada em `/home/henrique/.local/share/bauer-tool-gateway-live-canary`, usando a imagem de candidato `bauer-tool-gateway:sprint84-staging-zkvvzddx` (`sha256:fda39006d69ae340c9270e646caa4d4ff45868aec4dce3c68912380e5f89c36b`). O projeto e o arquivo marcador são sintéticos; há um único operador permitido e `allow_all=false`.
- O Bauer e o AgentOS dedicados estão saudáveis, sem portas publicadas e sem socket Docker ou volumes de produção. O Bauer do canário comprova o perfil AgentOS assinado; uma chamada sem autenticação ao Tool Gateway recebe HTTP 401. As credenciais de Telegram e Gateway estão somente no Bauer; o AgentOS recebe a chave de modelo e as chaves de identidade/atestado necessárias.
- `getMe` autenticou o bot. O cursor do poller foi transferido após parar o poller antigo; uma consulta não consumidora encontrou zero updates pendentes. Há exatamente um poller: o novo canário. O processo antigo está parado; a API Bauer de produção permanece saudável.
- Foi criado backup protegido em `/home/henrique/.local/share/bauer-backups/tool-gateway-live-canary-20261115-161750`; os 13 snapshots SQLite passaram em `PRAGMA quick_check`. O container `bauer-agent` está temporariamente com política de reinício `no` para impedir um segundo poller durante o canário; restaurar `unless-stopped` somente após fechar a janela do canário e desabilitar o auto-start Telegram antigo, ou após rollback ordenado.
- Esta ativação libera apenas leitura do projeto sintético, não escrita nem projetos reais. A validação de resposta ponta a ponta depende do operador enviar no Telegram: `Leia gateway-canary-marker.txt e retorne somente o marcador.` Resultado esperado: `BAUER_TELEGRAM_GATEWAY_CANARY_READ_OK`. Só a resposta real inicia a contagem dos 20 turnos; não tratar healthcheck, `getMe` ou a prova do perfil como turnos concluídos.

## Validação de código — 2026-11-15

- `uv sync --frozen --extra dev`, `uv run pytest tests/ -q --tb=short` e ambos os comandos Ruff do `AGENTS.md` passaram na branch da sprint. Os testes de cancelamento confirmam que o AgentOS não recebe cancelamento enquanto a ferramenta mantém um efeito em andamento; após a ferramenta assentar, o Bauer cancela a run pausada e não envia continuação.
- Isso valida o código local; não substitui os gates operacionais listados acima. Manter escrita e alteração do adapter padrão desligadas até completar a validação humana do canário, os 20 turnos e o rollback documentado.
