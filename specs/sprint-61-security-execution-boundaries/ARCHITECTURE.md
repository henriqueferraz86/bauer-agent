# ARCHITECTURE — Sprint 61: fronteiras de execução

## Fluxo do dispatcher (`ToolRouter.execute`) depois da sprint

```text
parse → resolve fn (registry externo > built-in), guarda _ext_def
  → allowlist de contexto
  → HARDLINE (run_command/execute_code/process)
  → runtime policy: _policy_operation_for_tool(name)
                    or _extension_approval_operation(name)   ← T08 (tool.approval)
  → budget max_tool_calls (pulado se INNER_GOVERNED_CALL)    ← T01
  → command guards
  → dry_run: tool_effects.call_has_side_effects(name, args, external=_ext_def,
             capability_permission=…) → DryRunResult          ← T06
  → App Factory gate
  → aprovação LLM se tool_security(name)["approval"]          ← T08 (efetiva)
  → handler (eventos com tool_security efetivo)               ← lote 6
```

## cronjob run (T01)

```text
cronjob run(name)
  → load_internal_json (.bauer_cronjobs.json, confinado — sprint 59)
  → _cronjob_run_governed(job, args)
       shell: pré-check denylist; exige "run_command" registrado
       python: execute_code {code, timeout: 60}
       repassa approval_id
  → tools.base.execute_governed(router, inner, inner_args)
       INNER_GOVERNED_CALL=True → router.execute({...})  (pipeline inteiro)
  → recarrega jobs, grava run_count/last_run/last_result
```

`execute_governed` e `INNER_GOVERNED_CALL` ficam em `bauer/tools/base.py`
para evitar import circular com `tool_router.py`. O flag só pula o
incremento de `_tool_call_count`. Nenhum outro gate olha para ele.

## Efeitos (T06): `bauer/tool_effects.py`

- `READ_ONLY_TOOLS`, `READ_ACTIONS` (por ação) e `EFFECT_TOOLS` são
  disjuntos. O teste de paridade exige que toda built-in registrada num
  router completo (shell e web) esteja em exatamente um deles.
- `call_has_side_effects` segue esta ordem:
  1. extensão com efeito → `True`;
  2. capability → `permission != "read"` (manifest revisado);
  3. leitura/ação de leitura → `False`;
  4. extensão nova com `side_effects=False` → `False`;
  5. qualquer outra coisa → `True`.

## Segurança efetiva (T08 + lote 6)

- `_effective_security(name)` (módulo `tool_router`):
  - sem extensão: `_TOOL_SECURITY[name]` ou o default histórico `read/low`
    (nome desconhecido não é executável);
  - extensão nova: a declaração dela;
  - override: o máximo de cada campo, pela ordem de `_PERMISSION_LEVELS` e
    `_RISK_LEVELS`, com nível desconhecido contando como o mais alto, e
    `approval = builtin or ext`.
- `ToolRegistry.ToolDefinition.side_effects: bool = True`, também aceito por
  `register()` e pelo decorator `tool()`.
- `core/policy/engine.py`: a regra default `tool.approval.ask` e
  `ASK_WHEN_UNMATCHED_OPERATIONS`, que vira `ask` quando nenhuma regra casa,
  mas não sobrepõe uma regra explícita. `config/policy.yaml` ganhou a mesma
  regra.

## Superfície de edição compartilhada

- `tool_router.py`: tabela `_TOOL_SECURITY`, o import de `tool_effects`, os
  blocos de dry-run, budget e LLM approval em `execute`, `tool_info`,
  `tool_security` e `_publish_tool_event`, mais uma linha em
  `_evaluate_runtime_policy` e duas funções novas antes de
  `_policy_operation_for_tool`. O mapa de operações em si não foi tocado
  (área de T02 da sprint 62).
- `policy/engine.py`: uma regra default, a constante nova e um ramo antes do
  `allow` final.
