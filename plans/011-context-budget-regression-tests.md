# Plan 011: Testes de regressão para budget/tail/`shrink_budget` do ContextManager

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report. When done, update this
> plan's status row in `plans/README.md`. This is a TESTS-ONLY plan — do NOT
> modify `bauer/context_manager.py`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/context_manager.py`
> If `bauer/context_manager.py` changed, compare "Current state" excerpts against
> live code before proceeding; on mismatch, treat as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (só adiciona testes)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

O `ContextManager` teve DOIS bugs reais de budget documentados no próprio
código, e nenhum dos dois tem teste de regressão guardando contra reintrodução:

1. **2026-06-10** — com budget 3072 (ctx 4096), o tail fixo de 8192 era maior
   que o budget inteiro → o conjunto a comprimir ficava sempre vazio →
   compressão nunca disparava e o modelo travava com contexto cheio. Corrigido
   com `_tail_budget = min(TAIL_BUDGET_TOKENS, max(512, self._budget // 3))`.
2. **2026-07-02** — `applied_context=128000` (nominal), mas o endpoint free do
   OpenRouter corta em 65536; o histórico crescia até ~66k sem atingir o
   threshold de compressão e toda chamada passava a falhar com HTTP 400.
   Corrigido com o método `shrink_budget(provider_cap_tokens)`.

Ambas as correções são load-bearing para modelos locais/free (o público-alvo
do Bauer) e um refactor futuro pode reintroduzir qualquer uma silenciosamente.
Este plano adiciona os testes que faltam — sem tocar na lógica.

## Current state

- `bauer/context_manager.py` — compressão/orçamento de contexto. Lógica
  relevante:

```python
# bauer/context_manager.py:100-111 (__post_init__)
    def __post_init__(self) -> None:
        effective = self.applied_context or PROVIDER_CONTEXT_WINDOWS.get(self.provider, 32768)
        self._budget = max(512, int(effective * 0.75))
        # tail = min(constante, 1/3 do budget), floor 512.
        self._tail_budget = min(TAIL_BUDGET_TOKENS, max(512, self._budget // 3))
```

```python
# bauer/context_manager.py:118-137 (shrink_budget)
    def shrink_budget(self, provider_cap_tokens: int) -> bool:
        """Reduz o budget quando o provider reporta uma janela REAL menor.
        Retorna True se o budget foi de fato reduzido."""
        if provider_cap_tokens <= 0:
            return False
        new_budget = max(512, int(provider_cap_tokens * 0.75))
        if new_budget >= self._budget:
            return False  # cap reportado não é menor que o budget atual
        self.applied_context = provider_cap_tokens
        self._budget = new_budget
        self._tail_budget = min(TAIL_BUDGET_TOKENS, max(512, self._budget // 3))
        return True
```

- `ContextManager` é (provavelmente) uma dataclass com campos `applied_context`,
  `provider`, `system_prompt`. Confirme a assinatura de construção:
  `grep -n "class ContextManager\|applied_context\|system_prompt\|@dataclass" bauer/context_manager.py | head`.
- `TAIL_BUDGET_TOKENS` e `PROVIDER_CONTEXT_WINDOWS` são constantes de módulo
  (`grep -n "TAIL_BUDGET_TOKENS =\|PROVIDER_CONTEXT_WINDOWS =" bauer/context_manager.py`).
  Leia o valor de `TAIL_BUDGET_TOKENS` para calcular os valores esperados nos
  asserts.
- `_budget` e `_tail_budget` são atributos privados setados em `__post_init__` —
  os testes podem lê-los diretamente (`cm._budget`).

### Convenções do repo a seguir
- Testes em `tests/`, pytest, sem rede. Já existem
  `tests/test_context_manager*.py` — modele a estrutura de construção do
  `ContextManager` por eles (como instanciam, que args passam). Rode
  `ls tests/ | grep context` e leia um deles antes de escrever.
- Não mocke o LLM a menos que precise — estes testes exercitam só a aritmética
  de budget, que não chama modelo.

## Commands you will need

| Purpose   | Command                                                              | Expected |
|-----------|----------------------------------------------------------------------|----------|
| Testes    | `.venv/Scripts/python.exe -m pytest tests/ -k "context_manager" -q`  | all pass |
| Grep base | `grep -rn "shrink_budget" tests/`                                    | (0 hits antes deste plano) |

## Scope

**In scope** (apenas testes):
- `tests/test_context_budget.py` (criar) — ou adicionar uma classe a um
  `tests/test_context_manager*.py` existente, se preferir manter junto.

**Out of scope** (NÃO tocar):
- `bauer/context_manager.py` — este plano NÃO altera código de produção. Se você
  achar que a lógica está errada, PARE e reporte (não conserte aqui).

## Git workflow

- Branch: `advisor/011-context-budget-regression-tests`
- Commit style: conventional commits. Ex.:
  `test(context): regressão para tail-vs-budget (2026-06-10) e shrink_budget (2026-07-02)`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Confirmar a ausência de cobertura e ler as constantes

```
grep -rn "shrink_budget" tests/          # esperado: nenhum resultado
grep -n "TAIL_BUDGET_TOKENS =" bauer/context_manager.py   # leia o valor
```

Se `shrink_budget` JÁ tiver testes, PARE e reporte (o achado pode ter sido
resolvido desde o planejamento).

**Verify**: o grep em `tests/` retorna vazio.

### Step 2: Escrever os testes de regressão

Crie `tests/test_context_budget.py` cobrindo os casos do Test plan. Instancie
`ContextManager` do mesmo jeito que os testes existentes fazem.

**Verify**: `.venv/Scripts/python.exe -m pytest tests/test_context_budget.py -q` → all pass.

## Test plan

- Arquivo `tests/test_context_budget.py`. Modele a construção do
  `ContextManager` por `tests/test_context_manager*.py`.
- Casos:
  1. **Regressão tail-vs-budget (2026-06-10)**: crie um `ContextManager` com
     `applied_context=4096` (contexto pequeno). Verifique que
     `cm._tail_budget <= cm._budget` (o tail NUNCA pode ser >= o budget inteiro,
     senão a compressão nunca dispara). Verifique também `cm._tail_budget ==
     min(TAIL_BUDGET_TOKENS, max(512, cm._budget // 3))`.
  2. **Budget floor**: `ContextManager(applied_context=100)` → `cm._budget == 512`
     (floor) e `cm._tail_budget >= 512`.
  3. **shrink_budget reduz quando cap é menor (2026-07-02)**: crie com
     `applied_context=128000`, guarde `cm._budget`, chame
     `cm.shrink_budget(65536)` → retorna `True`, `cm._budget` diminuiu para
     `max(512, int(65536 * 0.75))`, e `cm._tail_budget` foi recalculado
     (`<= cm._budget`).
  4. **shrink_budget é no-op quando cap não é menor**: com
     `applied_context=8192`, `cm.shrink_budget(128000)` retorna `False` e
     `cm._budget` não muda.
  5. **shrink_budget ignora cap inválido**: `cm.shrink_budget(0)` e
     `cm.shrink_budget(-1)` retornam `False` sem alterar `cm._budget`.
- Verificação: `.venv/Scripts/python.exe -m pytest tests/test_context_budget.py -q`
  → all pass (5 casos).

## Done criteria

TODAS devem valer:

- [ ] `.venv/Scripts/python.exe -m pytest tests/test_context_budget.py -q` passa (5 casos)
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -k context_manager -q` continua passando
- [ ] `grep -rn "shrink_budget" tests/` agora retorna ≥1 ocorrência
- [ ] `git status` mostra APENAS o novo arquivo de teste modificado (nenhuma
      mudança em `bauer/`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte se:

- Os excerpts de `__post_init__` ou `shrink_budget` não baterem com o código
  atual (drift) — os valores esperados nos asserts dependem deles.
- `shrink_budget` já tiver testes (achado possivelmente resolvido).
- A construção de `ContextManager` exigir um LLM client obrigatório para
  instanciar (não deveria — o budget é calculado em `__post_init__` sem
  modelo); se exigir, reporte.
- Qualquer teste revelar que a lógica de budget está de fato QUEBRADA (ex.:
  tail > budget para algum `applied_context`) — NÃO conserte o código; reporte,
  porque isso vira um plano de correção separado.

## Maintenance notes

- Estes testes são a rede de segurança para qualquer refactor futuro da
  aritmética de budget/compressão. Se as constantes de reserva (25% output,
  floor 512, 1/3 tail) mudarem intencionalmente, atualize os valores esperados.
- O reviewer deve conferir que os asserts usam as constantes do módulo
  (`TAIL_BUDGET_TOKENS`) em vez de números mágicos, para não quebrarem se a
  constante for retunada.
