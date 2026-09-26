# Plano 058 — Cobertura dos caminhos de erro das filesystem tools

> Implementar conforme `specs/sprint-25-p3-tool-error-paths/`. Escopo pequeno
> e guiado por testes; não ampliar para tools não-filesystem nesta rodada.

## Status

**DONE**

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW/MEDIUM (paths de escrita; manter falha segura)
- **Depends on**: none
- **Category**: tests/correctness

## Motivação

O achado P3 da auditoria aponta falta de cobertura de file-not-found, timeout e
permissão. A inspeção confirma que arquivo ausente e timeout já têm cobertura
em vários testes da suíte; a lacuna concreta observada é `PermissionError` e
outros `OSError` nos handlers de `bauer/tools/fs.py`, onde o contrato
`ToolError` não está uniformemente exercitado.

## Escopo e STOP

- Escopo apenas operações filesystem em `bauer/tools/fs.py`.
- Não reabrir casos de timeout/subprocesso já cobertos.
- Não mudar sandbox ou política de autorização.
- STOP se a correção exigir redesign de escrita atômica ou mudança de API; isso
  deve virar plano separado.

## Critérios de aceite

- Testes determinísticos cobrem falhas de permissão/I/O representativas.
- Erros de sistema são apresentados como `ToolError`, com causa encadeada.
- Falhas específicas existentes e estado de arquivos permanecem corretos.
- Testes focados, suíte completa, Ruff e diff-check passam.

## Conclusão

As operações cobertas agora convertem falhas `OSError` em `ToolError` com
mensagem contextual e causa encadeada. Oito testes determinísticos de
`PermissionError` cobrem listagem, leitura, criação de diretório, escrita,
append, patch (leitura e gravação) e remoção. File-not-found e timeout
permaneceram cobertos pelos testes existentes. Validação executada em Windows:
suíte completa, Ruff bloqueante, Ruff de estilo e `git diff --check` passaram.
