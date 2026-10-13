# Revisão final independente — Sprint 27

Reaberta em 2026-10-13 por pedido do usuário para continuar com o time.
Escopo funcional da SPEC preservado; segurança continua excluída.
Baseline final anterior: 7.874 testes Python aprovados/16 skips, Ruff/mypy,
29 testes frontend, build e Chromium aprovados. HEAD permanece feb67a1.

## Responsabilidades

- kernel_runtime: produtores CLI/scheduler e timestamp terminal, K03/K04/K10.
- tools_catalog: integração HTTP/custo/turnos e erro nativo, WEB-04–07/K02–04.
- web_ui: lifecycle/modelo, normalização de usage/Git e contratos finais.
- Coordenador: reproduzir/corrigir findings e validar a integração final.

Cada finding exige reprodução hermética ligada a um item funcional já
documentado. Após reprodução, o coordenador distribuiu implementações com
propriedade explícita de arquivos; um agente distinto verifica a correção.
Nenhum finding autoriza ampliar para segurança.

## Status

Revisão independente **APROVADA**, sem findings funcionais abertos.
Validação global final **APROVADA**: 7.982 passed/16 skipped, Ruff/mypy,
frontend/build/Chromium e diff-check verdes. Nenhum item de segurança executado.

## Findings reproduzidos e corrigidos na retomada

- `/chat` legado: chamadas/usage_known não eram persistidos no sucesso.
- Native: falha sem usage perdia a contagem da tentativa; Ctrl+C perdia uso parcial.
  Getter/setter auxiliar falhando não pode interromper o provider; usage readonly
  não atualizado deve registrar tentativa desconhecida, sem cobrar valor stale.
- Native/bridge/fallback: tools executadas antes do erro sumiam do retorno; loop
  perdia logs em falha do callback e CLI em interrupção.
- Bridge: usage parcial e tentativas de retry se perdiam em falhas; usage anterior
  podia ser cobrado. Wrappers aninhados precisam distinguir client/model e não
  suprimir consumo de outro provider. Cancelamento de voz não deve iniciar retries.
- Persistência HTTP: OSError ao salvar sessão deixava runs abertos ou perdia
  métricas. Save/touch agora são parte do resultado do executor em `/chat`,
  `/loop` e `/v1`; worker `/stream` finaliza falha preservando consumo.
- `/loop`: contador dependente do callback omitira tools parciais e precisava
  manter total entre replans, inclusive no estado consultado pela UI.
- Chat: quota de localStorage em useEffect/reset derrubava a árvore React;
  persistência auxiliar tolera falhas e chat continua utilizável.

## Evidências obtidas

- kernel_runtime: 112 testes CLI/scheduler/timestamp; CLI real com falha parcial
  e scheduler cancelado preservam custo no Run e no ledger.
- tools_catalog: 181 testes response/provider/streaming após 30 regressões novas.
- web_ui: revisão independente bridge aprovada em 85 testes; frontend 29 testes,
  build, Chromium controlado com 5 verificações (inclui quota), 61 testes usage/Git.
- Coordenador: 112 testes integração HTTP/sessão/timeout/custódia aprovados;
  paridade dev 11, Ruff crítico/amplo e mypy 377 arquivos aprovados.

## Pareceres finais independentes

- tools_catalog: integração HTTP aprovada, 93 testes; inclui falhas de save,
  tools parciais, contabilidade, replan/live status, transcript, parâmetros e locks.
- web_ui: bridge aprovado, 85 testes após corrigir o isolamento client/model.
  Snapshot final native/CLI/loop aprovado em mais **154 testes**, com 37 novas
  regressões partial tools, 30 bridge, cost/usage, native/stream e fixture HTTP.
- kernel_runtime: implementação final verificada em **370 testes**; Ruff
  0.17.0 offline, mypy e diff-check aprovados. Ctrl+C mantém cancelled/exit 130,
  tools, chamadas, custo e duração; callback falho mantém erro e métricas.
- UI: 29 testes/frontend/build e cinco verificações Chromium aprovados; quota
  bloqueia 14 writes/1 remove sem derrubar chat, completar turno ou reset.

Nenhuma frente tem finding funcional pendente. Segurança segue excluída.
A última validação global do coordenador está em [VALIDATION.md](VALIDATION.md):
7.982 passed, 16 skipped, 323 warnings existentes, 112,60 s; sem falhas.
O baseline de 7.874 testes pertence à primeira rodada; o limite de uso dos
agentes naquela rodada foi superado pela revisão independente retomada.

## Verificação adicional de fechamento

Novo pedido de loop com o time: kernel_runtime conferiu os oito critérios K
em 191 testes e tools_catalog os três critérios T em 75 testes/Ruff; web_ui
reconferiu mapping dos seis critérios WEB/UI. Nenhum defeito funcional aberto.

O coordenador revalidou nove TODOs históricos do plano 023, com 29 regressões
e links locais válidos. tools_catalog encontrou duas inconsistências
documentais: nota de CI que precisava citar Ubuntu e Windows; comando Ruff de
CLAUDE.md sem dependência auxiliar. Ambos corrigidos; o comando passou offline.
Parecer independente final: **APROVADO**, 11 testes adicionais, nenhum finding
restante. Evidência e arquivos em [STATUS_RECONCILIATION.md](STATUS_RECONCILIATION.md).
