# ARCHITECTURE — Sprint 25: caminhos de erro de filesystem

## Visão geral

Tratar `OSError` no limite das chamadas I/O selecionadas em `fs.py`,
transformando-o em `ToolError` localizado. O tratamento fica perto da chamada
que pode falhar para preservar a operação/path no contexto; não se adiciona um
catch genérico ao dispatcher, que poderia esconder defeitos de programação.

## Componentes

- `bauer/tools/fs.py`: leitura, escrita, append, patch, listagem, criação de
  diretório e remoção.
- `tests/test_new_tools.py` e/ou módulo de teste dedicado: injeção determinística
  de `PermissionError` e asserção do contrato público de erro.
- `specs/sprint-25-p3-tool-error-paths/`: contrato, decisões e backlog.

## Fluxo

```text
tool filesystem → operação Path/shutil → OSError → ToolError contextualizado
                                      ↘ sucesso → saída atual preservada
```

## Decisões técnicas

- Capturar `OSError` apenas ao redor de operações I/O, sem capturar
  `Exception` genericamente.
- Preservar o encadeamento da exceção para diagnóstico.
- Simular falhas com monkeypatch, sem depender de ACLs ou `chmod`.
- Reutilizar os casos existentes de arquivo ausente e timeout; esta sprint
  foca nas falhas de permissão/I/O ainda não cobertas.

## Alternativas consideradas

- Catch global em `ToolRouter`: rejeitado por perder contexto e mascarar erros
  fora de filesystem.
- Teste por `chmod(0)`: rejeitado por comportamento diferente entre Windows,
  Linux, usuário administrador e runner root.
- Capturar toda exceção em cada tool: rejeitado por ocultar bugs de código.

## Riscos técnicos

- Alguns métodos podem já tratar `FileNotFoundError`; a alteração deve manter
  a precedência dessas mensagens específicas.
- Mensagens de `OSError` variam por SO; testes devem validar tipo e contexto
  estável, não texto nativo completo.
- Escrita que falha durante a gravação pode deixar arquivo parcial; escrita
  atômica está fora do escopo e deverá ser avaliada separadamente se necessário.

## Observabilidade

O `ToolError` segue o fluxo existente do router e do agente; nenhum log novo,
telemetria ou dependência é necessário.
