# SPEC — Sprint 59: confinamento de sessões, mídia e stores internos

Plano: [`plans/064-seguranca-auditoria-2026-10-12.md`](../../plans/064-seguranca-auditoria-2026-10-12.md),
lote 1 de [`docs/audits/2026-10-12/CORRECTIONS.md`](../../docs/audits/2026-10-12/CORRECTIONS.md).
Base: `origin/master` em `ddd608c`. Branch: `codex/sprint-59-security-confinement`.

## Problema

A auditoria de 2026-10-12 confirmou três fugas de confinamento. O plano 064
registrou que, em 2026-11-08, os scripts quebravam antes de chegar a elas.
Por isso a sprint começou reproduzindo cada uma no código atual.

| ID | Achado | Reprodução no `ddd608c` (antes da correção) |
|---|---|---|
| WEB-01 | `session_id='../victim'` lê e regrava `.jsonl` fora de `sessions/` | `bauer_web_crossreview_history.py` com Kernel ligado: `TRAVERSAL_KERNEL_ENABLED 200 answer-overwrite`, e `victim.jsonl` externo recebeu `overwrite` + resposta. **Reproduz.** |
| T04 | `send_message` passa mídia de fora do workspace ao bridge e ao outbox | Bridge fake recebeu `/tmp/.../outside.txt`. Sem bridge, a mensagem foi enfileirada no outbox com o path cru. **Reproduz.** |
| T05 | stores internos seguem symlink | `.bauer_skills.json` → arquivo externo: `skill_manage create` **regravou o arquivo externo**. `.bauer_cronjobs.json` → externo: `cronjob list` **leu o arquivo externo**. `.bauer_memory.json` → externo: **já estava bloqueado** desde `6f68035` (checagem `resolve()` em `_memory_path`, com `ToolError`), mas a checagem e o `write_text` eram passos separados (TOCTOU) e a escrita não era atômica. **Reproduz em skills/cron; parcial em memory.** |

O probe `bauer_audit_tools_probe_extra.py` lia `/tmp/bauer_audit_tools_probe.py`.
Agora ele usa um caminho relativo ao próprio arquivo e continua depois dos
casos corrigidos (imprime `*_REJECTED`), então chega também aos casos de
outras sprints (T02/T03/T08).

## Objetivo

O ID de sessão, a mídia de saída e os stores internos não podem apontar para
fora da raiz deles, em nenhum transporte. A rejeição acontece antes do LLM, da
run, do bridge, do outbox e do upload.

## Escopo

1. **WEB-01: validação única de `session_id`** (`bauer/session_ids.py`).
   - Formato: `[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,199}`, sem terminar em `.`, e sem
     nome de dispositivo reservado do Windows (`con`, `nul`, `com1`…). Isso
     recusa absoluto, `..`, `/`, `\`, drive `C:\`, UNC, nome oculto, espaço,
     NUL e não-ASCII.
   - `SessionStore` (JSONL) valida em `turn/save/load/delete/exists` através de
     um único `_path()`. Também confere containment depois do `resolve` e
     recusa symlink no componente final (ver T05). `list_sessions` ignora
     symlinks e nomes inválidos.
   - `SqliteSessionStore` valida em `save/load/delete/exists`. A migração de
     JSONL legado ignora symlink/hardlink e nomes inválidos.
   - Transportes:
     - HTTP `/chat` e `/loop` (validator Pydantic → 422).
     - `/stream` e `/chat/context` (query → 400).
     - `/v1/chat/completions` (corpo → 422, header `X-Hermes-Session-Id` → 400).
     - `/sessions/{id}` e `/sessions/{id}/messages`, pelo store, com handler
       global `InvalidSessionIdError` → 400.
     - CLI `bauer agent --resume --session-id` (erro + exit 1).
     - Canais Telegram/Discord/gateway, pelo `SqliteSessionStore` (as chaves
       `tg:<id>`, `tg-project-<hash>-<id>`, `dc:<id>` e `agent:<id>:<slot>`
       continuam válidas).
2. **T04: mídia de saída**
   - `send_message.media_path` e `social_post.media_paths` passam por
     `_sandbox`, a mesma fronteira do `read_file`, e precisam ser arquivos.
     Bridge, outbox e Postiz recebem só o caminho absoluto resolvido. O pump
     do outbox deixa de depender do CWD.
   - Resposta do Telegram sem projeto vinculado: `[media: …]` também fica
     confinado ao workspace do router do gateway (antes, `allowed_root=None`
     aceitava qualquer absoluto).
3. **T05: stores internos** (`bauer/confined_files.py`, usados por memory,
   skills, cronjob e sessões JSONL)
   - A leitura usa `O_NOFOLLOW` no POSIX, então a checagem e a abertura são
     uma única syscall. No Windows, compara `lstat` com `fstat`. Recusa
     arquivo não regular e hardlink (`st_nlink > 1`).
   - A gravação é atômica: temporário exclusivo no mesmo diretório e depois
     `os.replace`, que troca a entrada e **nunca escreve no alvo de um
     symlink**, mesmo que ele apareça entre a checagem e a troca. Symlink já
     existente é recusado com erro, em vez de ser substituído em silêncio.
   - Nas tools, a violação vira `SandboxError` (subclasse de `ToolError`).

## Decisões registradas

- **Symlink em store interno é sempre recusado.** Isso vale mesmo quando ele
  aponta para dentro da raiz. Os arquivos `.bauer_*.json` e
  `sessions/*.jsonl` pertencem ao Bauer, e não existe caso de uso legítimo
  para um link ali. Aceitar só links "internos" exigiria resolver e depois
  abrir em dois passos, que é justamente o TOCTOU que a correção remove. A
  raiz em si (workspace ou `sessions/`) pode ser um symlink: ela é resolvida
  uma vez.
- **Conteúdo corrompido continua virando `{}`** nos stores JSON (comportamento
  histórico). Só a violação de confinamento vira erro.
- **`:` em ID de sessão continua permitido**, porque os canais usam `tg:<id>`.
  No Windows, o nome do arquivo JSONL codifica `:` como `%3A` (`%` nunca é
  válido num ID), já que `:` ali significaria drive relativo ou stream NTFS.
  No POSIX o nome continua igual, então as sessões existentes seguem
  compatíveis.
- **Nenhuma tool ganhou privilégio.** `send_message`/`social_post` passaram a
  exigir o mesmo confinamento do `read_file`. Quem antes anexava arquivo de
  fora do workspace agora recebe `SandboxError` e precisa copiar o arquivo
  para o workspace. Não foi criada allowlist extra de leitura, porque a
  `CORRECTIONS.md` só a admite "se escopo extra for necessário", e nenhum
  fluxo interno precisou (o `image_generate` já grava no workspace).
- **Risco residual (T04):** o bridge abre a mídia pelo caminho já resolvido,
  sem symlinks. Trocar um componente desse caminho entre a validação e o
  envio exigiria escrita concorrente dentro do workspace. Fechar isso de vez
  exigiria passar um descritor aberto pela API dos bridges e do Postiz, o que
  está fora do escopo desta sprint.
- Testes antigos que codificavam o comportamento vulnerável, anexando mídia
  de `tmp_path` fora do workspace (`test_liso_backend`, `test_social_tools`),
  foram ajustados para colocar a mídia dentro do workspace. Nenhuma guarda
  foi relaxada.

## Fora de escopo

- T02/T03/WEB-02/WEB-03 (sprint 62), K07/K08/WEB-08/WEB-10 (sprints 60/63) e
  T01/T06/T08 (sprint 61).
- `SqliteSessionStore.delete` apaga vetores por prefixo `"{sid}:"`. Isso é
  um problema funcional (prefixo ambíguo) e não de confinamento, e foi
  registrado no BACKLOG.

## Critérios de aceite

- Um ataque a `.jsonl` externo é rejeitado antes do LLM e da run em `/chat`,
  `/stream`, `/loop`, `/v1` (corpo e header) e `/sessions/*`, com Kernel
  ligado e desligado. O arquivo externo fica intacto.
- Bridges, outbox e Postiz não recebem caminho externo: absoluto, traversal
  e symlink são rejeitados antes do envio, do enqueue e do upload. Mídia
  interna sem texto continua funcionando.
- Memory, skills e cron não leem nem alteram o alvo de um symlink externo em
  get/list/set/delete, create/update/view/list e create/list/run. A
  persistência normal continua funcionando.
- Gates do `AGENTS.md` verdes.

## Plano de validação

- `tests/test_sprint59_security_confinement.py`: 60 casos. 24 falham no
  código anterior (WEB-01, T04, T05 skills/cron e o tipo de erro de memory).
  Os demais travam o contrato positivo (IDs válidos, persistência normal) e
  o comportamento de `confined_files`.
- `docs/audits/2026-10-12/repro/bauer_web_crossreview_history.py`:
  `TRAVERSAL_KERNEL_ENABLED 422` e `victim.jsonl` intacto.
- `docs/audits/2026-10-12/repro/bauer_audit_tools_probe_extra.py`:
  `MEMORY_SYMLINK_REJECTED`, `SEND_MEDIA_REJECTED` e
  `SEND_MEDIA_RECEIVED_ABS_PATH None`.
- Gates: `uv sync --frozen --extra dev`, pytest completo, os dois Ruff e mypy.
