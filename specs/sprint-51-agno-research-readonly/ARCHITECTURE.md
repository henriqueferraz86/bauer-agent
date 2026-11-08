# ARCHITECTURE — Sprint 51

## Módulos

| Arquivo | Conteúdo |
|---|---|
| `agno_capabilities/families/research.py` | políticas de busca (leitura) e extratores `incompatible` |
| `agno_capabilities/families/github.py` | `GithubSettings`, adapter nativo `bauer.github`, factory guardada de `agno.github` |
| `agno_capabilities/families/mcp.py` | `McpSettings`, ações dinâmicas, cliente stdio com ambiente mínimo, cliente HTTP |
| `agno_capabilities/manifest.py` | `ToolkitPolicy.dynamic_actions` + `with_settings()` |
| `agno_capabilities/catalog.py` | resolução/listagem usam a policy efetiva (com ações da config) |
| `agno_capabilities/bridge.py` | recusa de segredo em argumentos de operações de saída |
| `agno_catalog.py` | `bauer.web_fetch` no catálogo Bauer |

## Fluxo GitHub

```text
bauer.github:get_file  →  resolve (settings obrigatória)  →  Function
modelo → ToolRouter (network.http) → ponte: scanner de segredo nos args
       → ensure_repo_allowed (antes da rede) → _safe_path → httpx (timeout,
         sem redirect, verify=shared_ssl_context) → campos selecionados
```

`_TRANSPORT` é a costura de teste (`httpx.MockTransport`).

## Fluxo MCP

```text
agno_capabilities.yaml (servers.<nome>.tools.<tool>.risk)
  → dynamic_actions → ActionPolicy(<srv>__<tool>, read|write+approval, mcp.call)
modelo → ToolRouter → ponte → _invoke
  → _CALLERS[transport] (stdio: _ScopedMcpClient com env mínimo; http)
  → erros McpError/timeout/malformado → ToolError
  → saída truncada + cabeçalho "não confiável"
```

O processo stdio é criado por chamada e encerrado no `finally`: não há
servidor ocioso nem estado compartilhado entre agentes.

## Riscos

- Servidor stdio ainda roda com o usuário do Bauer (sem sandbox de SO); o
  controle é a declaração explícita do comando pelo administrador.
- `max_result_chars` limita o volume, não o conteúdo: o cabeçalho de dado não
  confiável é mitigação de prompt-injection, não garantia.
