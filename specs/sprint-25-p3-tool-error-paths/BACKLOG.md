# BACKLOG — Sprint 25: caminhos de erro de filesystem

## Tarefas

### 1. Vetar superfície I/O e escolher testes sem duplicação

Status: concluída

Critérios:
- Mapear cada chamada I/O em `bauer/tools/fs.py` e handlers existentes.
- Confirmar que file-not-found e timeout existentes não serão reimplementados.
- Fixar os casos de permissão/I/O por monkeypatch.

### 2. Implementar contrato de erro

Status: concluída

Critérios:
- Converter somente `OSError` pertinente em `ToolError` contextualizado.
- Preservar exceções específicas já existentes e encadeamento.
- Não alterar paths, sandbox, nem resultados de sucesso.

### 3. Testes e regressão

Status: concluída

Critérios:
- Cobrir operações representativas de leitura e mutação.
- Verificar tipo, contexto estável e estado do arquivo após a falha.
- Rodar testes focados e a suíte completa.

### 4. Revisão e gates

Status: concluída

Critérios:
- Revisão de segurança/qualidade concluída.
- Ruff, suíte e `git diff --check` passam.
- Atualizar status do plano #058 e este backlog.
