# 060 — Auditoria profunda do Bauer com equipe e navegador

Data: 2026-10-12. Status: CONCLUÍDO. Escopo: auditoria e plano de correção.

## Objetivo

Encontrar inconsistências, erros, bugs e duplicações problemáticas com evidência
reproduzível. Exercitar o produto publicado pelo servidor em Chromium real,
incluindo um modelo Ollama real quando disponível. Produzir um relatório que
distinga defeitos confirmados, limitações de ambiente e decisões documentadas.

## Método e responsabilidades

Skill aplicada: `spec-driven-project-setup` (`.agents/skills/spec-driven-project-setup/SKILL.md`).

| Frente | Responsável | Entrega |
| --- | --- | --- |
| Coordenação, ambiente e navegador | agente principal | baseline CI, jornadas, capturas, consolidação |
| Kernel, runtime, providers e memória | audit_kernel | evidências de limites, estados, persistência e contratos |
| Ferramentas e duplicação | audit_tools | inventário, schemas, exposição, políticas e deduplicação |
| HTTP, autenticação e interface | audit_web | contratos, sessões, SSE, erros e hipóteses para o navegador |

## Sequência

1. Registrar commit/branch e estado inicial; ler instruções e auditorias anteriores.
2. Preparar ambiente com `uv sync --frozen --extra dev`.
3. Executar suíte completa e os dois gates Ruff; medir também frontend existente.
4. Auditar as três frentes em paralelo, com reproduções herméticas em `/tmp`.
5. Subir instância separada em loopback, com home/config/workspace/sessões em `/tmp`.
6. Abrir SPA empacotada com Chromium e testar cadastro/login/logout, navegação,
   conversa/SSE, ferramenta sobre arquivo de teste, persistência e autonomia/stop.
7. Usar provider controlado em uma segunda instância para falhas e cancelamentos
   determinísticos; identificar explicitamente quais jornadas usam modelo real.
8. Conferir os achados entre código, testes, comportamento e histórico; rejeitar
   alegações sem trigger ou que descrevam uma escolha intencional.
9. Entregar relatório, artefatos e backlog priorizado de correções com regressões.

## Critérios de aceite

- Todos os comandos têm resultado e ambiente registrados.
- Há execução em navegador real, sem tratar TestClient como prova de E2E.
- Cada achado confirmado tem severidade, trigger, arquivo/linha, impacto e solução.
- Duplicação distingue aliases e backends documentados de contratos divergentes.
- Falhas de modelo/rede são descritas separadamente das falhas do produto.
- O relatório indica cobertura executada e o que ficou sem validação.
- O índice e o status deste plano são atualizados ao final.

## Limites e condições de parada

- Nenhuma alteração de código de produto faz parte desta auditoria.
- Não modificar configuração, credenciais, sessões ou serviços reais do usuário.
- Não disparar mensagens externas, publicar, executar comandos destrutivos ou
  instalar novos pesos de modelo. Testar mutações apenas em recursos temporários.
- Se um cenário exigir serviço externo ausente, registrar como não validado e
  continuar nas demais frentes. Não declarar o cenário aprovado.
- Scripts de browser e provas usam ambiente auxiliar sem mudar `uv.lock`.

## Entregáveis

- `docs/audits/2026-10-12/REPORT.md`: resultados, evidências e limitações.
- `docs/audits/2026-10-12/CORRECTIONS.md`: plano executável por prioridade.
- `docs/audits/2026-10-12/`: scripts e artefatos sanitizados reproduzíveis.

## Andamento

- [x] Equipe iniciada e instruções lidas.
- [x] Ambiente sincronizado pelo lock (Python 3.12.13).
- [x] Baseline e análise paralela concluídos.
- [x] Jornadas Chromium com modelo real e provider controlado.
- [x] Consolidação, plano de correção e revisão das evidências.

## Resultado

30 achados reproduzidos: 20 P1 e 10 P2. Suíte Python: 7.697 aprovados,
15 pulados; gates Ruff, mypy e frontend passaram. Chromium validou cadastro,
login/logout, 16 telas, chat/SSE, leitura e escrita com Ollama real,
persistência e loop/stop; cenários controlados expuseram falhas de lifecycle.

Relatório: [REPORT.md](../docs/audits/2026-10-12/REPORT.md).
Plano de correções: [CORRECTIONS.md](../docs/audits/2026-10-12/CORRECTIONS.md),
status TODO. A auditoria foi concluída; os defeitos documentados não foram
corrigidos nesta tarefa.
