# ARCHITECTURE — Sprint 50: catálogo e ponte governada

## Módulos novos (`bauer/core/runtime/agno_capabilities/`)

| Módulo | Papel | Importa SDK? |
|---|---|---|
| `discovery.py` | Lê `agno/tools/**/*.py` como texto (AST) via `PathFinder`; `module_spec_exists` resolve imports segmento a segmento sem executar pacotes | não |
| `manifest.py` | `ToolkitPolicy`/`ActionPolicy` revisados em código; `families/` registra políticas por família (sprints 51+) | não |
| `settings.py` | `agno_capabilities.yaml` estrito; modelos por capability (`register_settings_model`); erros só com nomes de campos | não |
| `credentials.py` | presença/resolução de credencial por nome (env → `CredentialPool($BAUER_HOME)`) | não |
| `catalog.py` | estados, `parse_capability_ref`, `resolve_capability` fail-closed, `catalog_entries`, `validate_agent_tools`, registro de fluxos de aprovação | não |
| `bridge.py` | factories (allowlist em código), `materialize`, `to_agno_functions` | só ao materializar uma capability revisada |
| `doctor.py` | resumo para `bauer doctor` | não |

## Fluxo

```text
AgentSpec.tools ["agno.calculator:add"]
  → AgnoRuntimeAdapter._map_tools
      → _is_governed_capability (agno.* ou id revisado)
      → bridge.materialize
          → resolve_capability (manifesto + estado + ações) ── falha ⇒ RuntimeAdapterError
          → factory (SDK: include_tools=ações; ou adapter Bauer)
      → Function(name="agno_calculator__add", entrypoint=guard(entry))
modelo chama a Function
  → entry: router = router_for_session(); register_capability_tool (idempotente)
  → router.execute_native_call(name, kwargs)
      → contexto (grant por agente; deny por nome) → PolicyEngine(operation)
      → aprovação (se ask) → budget de tools → timeout → scanner de segredos
      → eventos tool.call.* com {"arg_keys"}, capability, action, operation
```

## Mudanças em módulos compartilhados (mínimas e aditivas)

- `tool_router.py`: `register_capability_tool`/`has_capability_tool`;
  `_CAPABILITY_TOOLS` (nome → operação/capability/ação) consultado por
  `_policy_operation_for_tool`; `_telemetry_args`/`type_only_error` só para
  essas tools; `_is_tool_allowed_in_context` trata tool de capability
  registrada na instância como concedida, respeitando `deny`.
- `agno_adapter.py`: um ramo em `_map_tools` + dois helpers estáticos.
- `agno_catalog.py`: `list()` passa a anexar `catalog_entries()` (toolkits
  individuais) no lugar das pastas de família.
- `cli.py`: painel "Capabilities Agno" no `doctor` (best-effort).
- `desktop/src/screens/Agents.tsx`: rótulos dos novos estados e "faltando".

## Segurança

- Nome da tool é derivado do ID revisado (`<id>__<acao>`); colisão com tool
  existente é recusada; re-registro com outra policy é recusado.
- Erros do conector viram `ToolError` com tipo + texto redigido para o
  modelo; telemetria recebe só o prefixo/tipo.
- Saída limitada a 20 000 caracteres.
- Factory SDK só importa módulos `agno.tools.*` fixados no manifesto.

## Alternativas descartadas

- **Registrar tools no `ToolRegistry` global:** exporia a ação a todos os
  agentes (viola default deny).
- **Executar a função Agno direto e só emitir eventos próprios:** duplicaria
  policy/aprovação fora do ToolRouter (STOP do plano).
- **Seção em `config.yaml`:** ver SPEC, "Decisões".
