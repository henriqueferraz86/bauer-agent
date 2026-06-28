# Planos de Implementação — BauerAgent

Gerado pelo skill `/improve` em 2026-06-27 (commit `820322b`).
Execute na ordem abaixo, salvo dependências indicadas.
Cada executor: leia o plano completo antes de iniciar, respeite as condições
de STOP, e atualize sua linha de status ao concluir.

## Ordem de execução e status

| Plano | Título | Prioridade | Esforço | Depende de | Status |
|-------|--------|------------|---------|------------|--------|
| [001](001-fix-http-exception-detail.md) | Ocultar detalhes de exceção nas respostas HTTP 500 | P1 | S | — | DONE |
| [002](002-fix-api-key-timing-attack.md) | Substituir comparação de API key por hmac.compare_digest | P1 | S | — | DONE |
| [003](003-fix-info-endpoints-auth.md) | Adicionar guarda de auth nos endpoints informativos | P1 | S | — | DONE |
| [004](004-fix-xor-fallback.md) | Eliminar o fallback XOR silencioso em auth.py | P1 | S | — | DONE |
| [005](005-commands-integration-tests.md) | Testes de integração para os 35 módulos de bauer/commands/ | P1 | M | — | TODO |

Status válidos: `TODO` | `IN PROGRESS` | `DONE` | `BLOCKED (motivo)` | `REJECTED (motivo)`

## Notas de dependência

- 001, 002, 003, 004 são independentes entre si e podem ser executados em
  qualquer ordem ou em paralelo.
- 005 é independente dos demais, mas os novos testes servem como rede de
  segurança para qualquer refactor futuro de `bauer/commands/`.
- Planos futuros para `agent.py` (refactor P4 continuado) **devem** aguardar
  005 estar `DONE` — os testes de commands serão a rede de segurança.

## Achados considerados e rejeitados

- **StepResult sem validação em `--resume`** (`orchestrator.py:553`): real, mas
  só afeta estado corrompido em disco; impacto baixo comparado aos 5 planos.
  Candidato para próxima rodada.
- **Fallback silencioso no DAG circular** (`orchestrator.py:329`): comportamento
  defensivo intencional, mas sem aviso visível. Candidato para PR pequeno
  (adicionar um `console.print()` de aviso).
- **Chave de auth em arquivo texto plano** (`auth.py:307-314`): levantado como
  SEC-02. Esforço M e risco MED (requer integração com OS keyring). Adiado
  para próxima rodada de planejamento.
- **`AGENTS.md` vazio / sem `CLAUDE.md`**: achado DX-06. Baixo impacto técnico
  imediato; recomendado para próxima sprint de documentação.
- **`agent.py` com 3089 linhas**: tech debt real, mas Esforço L e Risco HIGH.
  Requer characterization tests (005) como pré-requisito.
- **Scripts `_fix_chat*.py` em `workspace/`**: dead code, não vale um plano.
  Delete manualmente se necessário.
- **`_quantfx_staging/`**: já gitignoreado, sem impacto.
- **Inconsistência de extras no `pyproject.toml`**: editorial, sem impacto funcional.
- **Pydantic v2 sem ADR**: baixa urgência, sem risco prático identificado.
