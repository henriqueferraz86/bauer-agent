# ARCHITECTURE — Sprint 58

| Peça | Arquivo |
|---|---|
| Classificação dos toolkits restantes | `agno_capabilities/families/catalog_coverage.py` |
| `ToolkitPolicy.blocked`, `credential_kwargs` | `agno_capabilities/manifest.py` |
| estado `blocked_by_policy` por policy | `agno_capabilities/catalog.py` |
| `SdkToolkitModule.init_args` (AST) | `agno_capabilities/discovery.py` |
| injeção de credencial, fallback `all=True` | `agno_capabilities/bridge.py` (`sdk_toolkit_factory`) |
| matriz, Markdown, snapshot e diff | `agno_capabilities/coverage.py`, `sdk_snapshot.json` |
| CLI | `bauer/commands/tools_cmd.py` (`tools capabilities`) |
| Desktop | `desktop/src/screens/Agents.tsx` (busca + filtro de estado) |

```text
bump do agno no uv.lock
  → discovery (AST) muda
  → tests/test_agno_catalog_coverage.py::test_snapshot_matches_installed_sdk falha
     com o diff (added/removed/changed)
  → revisor ajusta manifesto/políticas e roda `bauer tools capabilities --write-snapshot`
  → nada novo fica habilitado até revisão (unreviewed / ação não classificada)
```

Ordem de estado: `incompatible` → `blocked_by_policy` → (SDK ausente /
toolkit sumiu) → dependência → configuração/credencial → `ready`/`enabled`.
