# Plano de correção após a auditoria

Status: **17 correções funcionais concluídas e validadas; segurança excluída**.
Origem: [REPORT.md](REPORT.md), 30 achados confirmados. Plano de auditoria 060
concluído. Execução local na [Sprint 27](../../../specs/sprint-27-functional-audit-corrections/SPEC.md),
adaptada ao pedido explícito de excluir segurança. Os lotes abaixo são mistos;
as partes de segurança e suas dependências não foram executadas nesta manutenção.

## Regras de execução

1. Cada lote abre uma sprint/branch `codex/sprint-XX-*`, escolhendo o próximo
   número livre. Criar `SPEC.md → ARCHITECTURE.md → BACKLOG.md` antes de código.
2. Transformar as reproduções relevantes em regressões pytest herméticas,
   com `tmp_path`/`monkeypatch`. Não adicionar provider real à suíte.
3. Corrigir uma tarefa pequena por vez; atualizar evidências e backlog.
4. Validar no ambiente `uv sync --frozen --extra dev`, suíte e gates do AGENTS.
   Explicar/regularizar a instalação de Ruff antes de afirmar paridade literal.
5. Não avançar ao lote seguinte com falha nova. Se baseline falhar, corrigir
   ambiente primeiro, identificar regressão e registrar STOP até resolução.
6. Mudanças de dependência exigem `uv lock` e lock versionado. Nenhum lote
   necessita chamadas cloud ou publicação para comprovar as regressões.

## Sequência e critérios de aceite

| Lote | IDs | Prioridade | Tamanho | Dependência | Status |
| --- | --- | --- | --- | --- | --- |
| 1. Confinamento de sessões e arquivos | WEB-01, T04, T05 | P1 | M | — | EXCLUÍDO nesta execução; pendente |
| 2. Política, aprovação e simulação de tools | T01, T02, T03, T06, T08, WEB-02, WEB-03 | P1 | L | 1; pode investigar em paralelo | EXCLUÍDO nesta execução; pendente |
| 3. Estados terminais e admissão | K01, K02, K08, K09 | P1/P2 | L | 2 | K01/K02/K09 implementados; K08 excluído |
| 4. Contabilidade de consumo | K03, K04, K05, K10 | P1/P2 | L | 3 | DONE funcional |
| 5. Gates Git reais | K06, K07 | P1 | M | 4 | K06 implementado; K07 excluído |
| 6. Catálogo, schemas e dedup | T07, T09, T10 | P2 | M | 2; integração final após 5 | Funcional implementado; metadados de segurança excluídos |
| 7. Motor HTTP e concorrência de sessões | WEB-04, WEB-05, WEB-06, WEB-07 | P1/P2 | L | 3, 4, 6 | DONE funcional |
| 8. Lifecycle da UI e validação final | WEB-08, WEB-09, UI-01, WEB-10, paridade Ruff | P2/P3 | M | 7 | WEB-09/UI-01/Ruff implementados; WEB-08/10 excluídos |

## Remediação funcional da Sprint 27

Os 17 itens funcionais autorizados têm código e regressões: K01–K06, K09/K10,
T07 (cache), T09 (schemas), T10, WEB-04/05/06/07/09 e UI-01. Contabilidade inclui
integração de CLI, scheduler e HTTP, além do Kernel. Reserva de vagas exige
run persistido; callers legados sem run_id apenas validam capacidade.

`/v1` compartilha o motor governado para native/bridge/tools/fallback. O SSE
continua bufferizado. Transcript completo é autoritativo por padrão; incremental
é explícito. Parâmetros não suportados são rejeitados com 422. Locks entre
processos usam flock no POSIX; Windows garante apenas serialização no processo.
Essas escolhas e o contrato público estão documentados no README e na sprint.

A revisão cruzada encontrou custo perdido em falhas HTTP, custo cumulativo em
replan de `/loop`/CLI, métricas perdidas no scheduler e races de UI; cada caso foi
corrigido com regressão. Chromium local controlado aprovou navegação, persistência,
reload e HUD, sem provider real nem chamadas externas. Resultado final e limites
estão registrados em [VALIDATION.md](../../../specs/sprint-27-functional-audit-corrections/VALIDATION.md):
7.982 testes Python aprovados/16 skips, ambos gates Ruff e mypy aprovados,
29 testes frontend, build e cinco verificações Chromium aprovados. A revisão
retomada em 2026-10-13 corrigiu gaps de tools/usage parciais, retries, Ctrl+C,
callbacks, persistência HTTP e quota de localStorage. A integração final recebeu
aprovação independente do time; não há finding funcional autorizado aberto.
Histórico e evidências em [REVIEW.md](../../../specs/sprint-27-functional-audit-corrections/REVIEW.md).

### Lote 1 — confinamento

- Validar session IDs em um único ponto e em todos os transportes; rejeitar
  absolutos, traversal, separadores inválidos e symlink que escape da raiz.
- Resolver mídia e stores internos sob workspace antes de leitura/escrita/envio.
- Usar gravação atômica com comportamento definido para symlink; não deixar
  validação e abertura do arquivo como pontos independentes vulneráveis.
- **Aceite:** ataque a `.jsonl` externo rejeitado antes de LLM/run; bridges e
  outbox não recebem path externo; memória não altera alvo de symlink externo.
- **Regressões:** IDs válidos, ausência de ID, Windows/Unix separators, paths
  externos e internos, symlink, media sem texto, persistência normal.

### Lote 2 — fronteiras de execução

- Cronjob run deve reutilizar autorização/validação de shell e código; respeitar
  disponibilidade `shell_enabled`, allowlist, policy e approval.
- Mapear `http_request` para operação de política correta; remover headers
  sensíveis ao trocar origem de redirect, incluindo host/scheme/port.
- Declarar efeito por ação para dry-run com default seguro de extensão.
- Aplicar definição de aprovação do registry efetivo no dispatcher.
- Iniciar runs de time com POST+CSRF; GET de eventos apenas observa.
- Preservar garantia local em switch, OAuth, fallback e roteamento posterior.
- **Aceite:** todos os cenários T01/T02/T03/T06/T08/WEB02/WEB03 são negados ou
  simulados antes do handler/transporte; ações autorizadas continuam funcionando.
- **STOP:** se for necessário ampliar privilégio de uma tool ou reinterpretar
  `--local`/dry-run, registrar decisão explícita na SPEC; não silenciar guardas.

### Lote 3 — estados e admissão

- CAS para terminais e transições intermediárias; cancelamento não pode ser
  convertido em sucesso por executor/gate que termina depois.
- Revalidar kill-switch/policy mutável imediatamente antes de continuação e
  cada retry/replan, preservando aprovações que continuam válidas.
- Excluir candidato da contagem e reservar vagas atomicamente; compartilhar
  liveness baseado em heartbeat entre recovery e admissão.
- **Aceite:** limite 1 permite exatamente um run útil; segundo concorrente é
  negado; heartbeat vivo conta; run cancelado permanece terminal único;
  kill-switch posterior à admissão bloqueia execução e novas tentativas.
- **Regressões:** threads e managers distintos, gate lento, queued/running/
  waiting_approval, recovery de run realmente morto, estados restaurados.

### Lote 4 — consumo

- Separar liquidação de custo do resultado; registrar consumo de falhas,
  cancelamentos, gates e retries sem dupla contagem.
- Definir métricas incrementais por attempt ID; agregar tools/LLM/custo.
- Normalizar usage dos providers no cost sink, preservando custo reportado e
  semântica de cache; garantir provider local com custo zero.
- Persistir duração total incluindo gates; distinguir duração de executor.
- **Aceite:** duas tentativas 0,40 somam 0,80; failed/cancelled 0,40 reduzem
  budget; usage Anthropic e normalizado convergem; run com gate lento registra
  duração total; nenhum run terminal mantém reserva injustificada.
- **Regressões:** replan, fallback, gate reprovado, erro parcial, consumo
  conhecido/desconhecido, hooks duplicados e chamadas locais.

### Lote 5 — gates

- Enumerar untracked individualmente e parsear caminhos/renames corretamente.
- Diferenciar Git ausente, HEAD inexistente e erro operacional; escanear
  staged/worktree/untracked mesmo antes do primeiro commit.
- **Aceite:** forbidden descendente de diretório novo reprova; segredo
  sintético em repo vazio reprova; falha do scanner não passa silenciosamente.
- **Regressões:** Git real temporário, spaces/quotes/unicode/rename, staging,
  árvore vazia, timeout, arquivos permitidos e output sem segredos.

### Lote 6 — definição única de tool

- Produzir uma definição efetiva de handler, schema, segurança, policy,
  efeitos e cache após overrides/allowlist; evitar listas independentes.
- Corrigir kanban_read/kanban_write e remover metadados órfãos confirmados.
- Declarar tipos array/items e required globais reais; preservar sanitização
  de schemas específica dos providers sem distorcer contrato.
- **Aceite:** cada tool exposta possui exatamente um schema compatível;
  override muda schema e segurança; write invalida reads e nunca é replayado.
- **Regressões:** registry, override de built-in, allowlist, ações mínimas
  válidas de Kanban/process/memory/cron, social channels/tags arrays,
  TestsGate fora de repo Git e aprovações contextuais existentes.

### Lote 7 — sessão e transporte

- Serializar o ciclo load→execute→save por sessão ou usar versão/conflict;
  escrita atômica sozinha não resolve perda de mensagens.
- Definir `/v1 messages` como transcript autoritativo ou reconciliar prefixo;
  identificar modo incremental explicitamente se necessário.
- Compartilhar o motor de turno para batch e stream, com sinks de evento e
  custo, native/bridge, fallback, dedup, budgets e custódia conforme contrato.
- Implementar ou rejeitar parâmetros não suportados; não aceitar com 200 e
  ignorar. Preservar instruções system/developer sob política explícita.
- **Aceite:** dois turnos concluídos não perdem mensagens; batch/stream recebem
  mesmas capacidades e custo; SDK full transcript não duplica histórico;
  modelo, limites e parâmetros têm comportamento observável e documentado.
- **Regressões:** mesma sessão em `/chat`, `/stream`, `/loop`, `/v1`; sessões
  diferentes em paralelo; desconexão/timeout; native/bridge; Kernel on/off.

### Lote 8 — UI, recursos e encerramento

- Definir retenção de chat após logout/expiração; limpar storage e broadcasts
  conforme política, considerando servidor single-admin.
- Gerenciar AbortController por turno ou persistir continuidade em background
  com run rastreável; sair da tela não pode perder resposta e session ID.
- Atualizar HUD após switch por Chat/Models/OAuth; refletir provider real.
- Reproduzir WEB-10 com ASGI receive contado e threshold pequeno; aplicar
  limite antes do parser se confirmado, incluindo multipart/chunked.
- Regularizar Ruff no setup e revisar docs desatualizadas de Kernel/store.
- **Aceite:** browser real repete cadastro/login/logout, tool write/read,
  reload, navegação durante stream e loop/stop/reload sem os bugs encontrados.
- **Validação final:** suíte completa, gates, frontend, smoke E2E com Ollama
  instalado e cenários controlados. Registrar limites de Windows/audio/cloud;
  não marcá-los aprovados apenas por terem mock em teste.

## Equipe para executar as correções

- Responsável por Kernel/runtime: lotes 3–5.
- Responsável por tools/policy: lotes 1–2 e 6.
- Responsável por HTTP/UI: lotes 7–8.
- Coordenador/revisor: integra lotes, executa regressões e E2E e atualiza status.

Trabalho paralelo pode investigar ou preparar specs; edições em contratos
compartilhados devem ter um dono. Revisão independente precisa conferir a
evidência original e a regressão antes de marcar qualquer ID como corrigido.
