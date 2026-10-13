# SPEC — Sprint 27: correções funcionais da auditoria local

## Problema e objetivo

A auditoria de 2026-10-12 documentou defeitos reproduzíveis de concorrência,
estados, contabilidade, catálogo de tools e sessões/UI. A manutenção anterior
corrigiu apenas a contagem do próprio candidato em K01. Concluir as correções
funcionais e de qualidade autorizadas, com regressões herméticas e revisão.

## Escopo autorizado

- K01/K02/K09: admissão concorrente, liveness e estados terminais consistentes.
- K03/K04/K05/K10: consumo de tentativas e falhas, usage nativo e duração total.
- K06: enumeração correta dos arquivos Git para o gate de qualidade de escopo.
- T07 (cache), T09 (catálogo/schema), T10 (tipos e campos condicionais).
- WEB-04/05/06/07: concorrência, transcript e paridade batch/stream `/v1`.
- WEB-09 e UI-01: ciclo do stream e atualização do modelo exibido.
- Paridade do roteiro Ruff e documentação desatualizada sobre Kernel/store.
- Reconciliação dos status históricos de `plans/023-auditoria-completa-2026-07.md`
  nos itens 19, DOCS, 09, 17, 20, 16, 08, 11 e 18, incluindo paridade do
  comando Ruff de CLAUDE.md: marcar concluído somente
  com implementação atual e evidência local. Detalhes originais permanecem
  como contexto histórico; itens de segurança e features não são executados.

## Fora de escopo e restrições

Itens relacionados a segurança continuam excluídos conforme pedido anterior:
WEB-01/02/03/08/10, T01/02/03/04/05/06/08, K07/K08, além de metadados de
permission/risk/approval de T07/T09. Os pedidos de continuar e trabalhar com o
time não revogaram a exclusão; ampliação exige instrução explícita e atualização
desta SPEC antes de editar.
Não acessar sistemas externos, credenciais ou configurações sensíveis. Não fazer
commit/push. Branch local apenas. Não instalar pacotes pela rede nem usar providers
reais; provas de HTTP/providers usam transportes controlados. Temporários de testes
seguem `tmp_path` padrão, fora da árvore Git, para preservar hermeticidade.

## Fontes

`docs/audits/2026-10-12/{REPORT,KERNEL,TOOLS,TOOLS-REVIEW,WEB,WEB-REVIEW,CORRECTIONS}.md`.
`plans/023-auditoria-completa-2026-07.md` e a pendência de reconciliação
registrada em `plans/README.md` são fontes adicionais de qualidade documental.
Os relatórios históricos preservam o estado auditado; o backlog registra remediação.

## Skill obrigatória e equipe

- Skill: `.agents/skills/spec-driven-project-setup/SKILL.md`.
- `kernel_runtime`: estados/admissão/contabilidade e seus testes.
- `tools_catalog`: catálogo, schemas e dedup e seus testes.
- `web_ui`: sessões, transporte, UI e seus testes.
- Coordenador: K05/K06, documentação, integração e validação global.
- Revisão independente cruzada após cada frente, sem agentes adicionais.

## Critérios de aceite

1. Limites 1/2 permitem todas as vagas; concorrentes não excedem o cap; heartbeat
   recente preserva a vaga de um run longo.
2. Cancelamento mantém um único terminal mesmo após executor ou gate tardio.
3. Consumo conhecido de falhas/gates/retries é persistido e liquidado uma vez;
   duas tentativas de 0,40/2 tools resultam em 0,80/4 tools. Inclui tools
   executadas antes do erro, interrupção pelo usuário e falhas de persistência
   após a execução, nos motores native/bridge, CLI e HTTP.
4. Usage Anthropic bruto e canônico convergem, custo reportado é preservado e
   providers locais permanecem gratuitos; tempo inclui gates.
5. Git real enumera arquivos untracked e nomes especiais/renames corretamente.
6. Cada tool exposta possui schema efetivo único; arrays são arrays; campos
   condicionais não se tornam required global; escrita Kanban invalida leitura.
7. Turnos concorrentes não perdem histórico; transcript completo não duplica;
   batch/stream mantêm capacidades e parâmetros observáveis sem código duplicado.
8. Navegação não perde conclusão/sessão do stream e modelo exibido acompanha backend.
9. Suíte Python, Ruff e testes/build frontend disponíveis offline passam; casos
   não executáveis no ambiente são explicitamente registrados sem falso aceite.

## Validação

`UV_OFFLINE=1 uv sync --frozen --extra dev`; regressões dirigidas por tarefa;
`UV_OFFLINE=1 uv run --frozen pytest tests/ -q --tb=short`; os dois gates Ruff
do AGENTS (usar `--with ruff` offline se necessário); mypy e diff-check.
Frontend via scripts locais já instalados. Testes concorrentes usam barreiras/
eventos, evitando asserções de velocidade. Nenhuma tarefa é encerrada sem revisar
e verificar critérios; falha nova bloqueia avanço até causa identificada.
