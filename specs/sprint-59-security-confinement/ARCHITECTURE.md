# ARCHITECTURE — Sprint 59: confinamento

## Visão

```text
transporte (HTTP /chat /stream /loop /v1 /sessions, CLI, canais)
  → session_ids.validate_session_id      ← único ponto de formato
  → SessionStore._path / SqliteSessionStore
       → confined_files.read_text / write_text_atomic   ← containment + no-follow
tools (memory, skills, cronjob)
  → tools.base.load_internal_json / save_internal_json
       → confined_files (mesma semântica)
tools de saída (send_message, social_post) e resposta do Telegram
  → ToolRouter._sandbox / extract_outbound_media(allowed_root=workspace)
  → bridge / outbox / Postiz recebem só o path absoluto resolvido
```

## Módulos

### `bauer/session_ids.py` (novo)

- `validate_session_id(value) -> str`: levanta `InvalidSessionIdError`
  (subclasse de `ValueError`). Por isso funciona direto como validator
  Pydantic, que converte em 422.
- `optional_session_id(value)`: `None`/`""` viram `None`, preservando o
  comportamento "sem ID → `new_id()`". Usado nos validators de `ChatRequest`,
  `OAICompletionRequest` e `LoopStartRequest`, e nos parâmetros de query e
  header.
- `is_valid_session_id(value) -> bool`: usado por `list_sessions`, pela
  migração SQLite e pelo CLI.

### `bauer/confined_files.py` (novo)

- `_confined_target(path, root)`: resolve **só o diretório pai** e exige que
  ele fique dentro de `root.resolve()`. O componente final nunca é resolvido.
- `read_text(path, root, errors=)`: abre com
  `O_RDONLY | O_NOFOLLOW | O_BINARY`. `ELOOP` vira `ConfinementError`. Sem
  `O_NOFOLLOW`, faz `lstat` antes e confere `samestat` com o `fstat` do
  descritor. Exige `S_ISREG` e `st_nlink == 1`. Arquivo ausente devolve `None`.
- `write_text_atomic(path, text, root)`: recusa symlink existente (`lstat`),
  depois `mkstemp` no diretório (O_EXCL, 0600), `fsync` e `os.replace`. Se a
  checagem perder a corrida, `os.replace` troca o link por um arquivo
  regular. O alvo nunca é aberto para escrita.
- `unlink` e `exists` operam sobre a entrada sem seguir o link.

### Integração

- `SessionStore._path(sid)`: valida e mapeia `:` → `%3A` só no Windows.
  `ConfinementError` do store vira `InvalidSessionIdError`, para o HTTP
  responder 400 pelo handler global.
- `server.py`: `@app.exception_handler(InvalidSessionIdError)` → 400, mais o
  helper `_session_id_param` para query. As mudanças em `server.py`,
  `server_chat.py` e `server_openai.py` são só de ingresso. Nenhuma lógica de
  turno foi alterada.
- `tools/base.py`: `load_internal_json` e `save_internal_json`. Erro de
  confinamento vira `SandboxError`, e erro de conteúdo vira `{}`.
- `MessagingToolsMixin._outbound_media_path`: `_sandbox(raw)` + `is_file()`.
- `TelegramBridge._process_scoped_message`: no modo sem projeto,
  `allowed_root` passa a ser o workspace do `_router` do backend (com
  fallback para o diretório do config).

## Ordem de rejeição (HTTP)

| Entrada | Onde rejeita | Status | Antes de |
|---|---|---|---|
| `/chat` body | `ChatRequest` validator | 422 | `store.turn`, run, LLM |
| `/loop` body | `LoopStartRequest` validator | 422 | run, thread do loop |
| `/v1` body | `OAICompletionRequest` validator | 422 | run, LLM |
| `/v1` header | `optional_session_id` no handler | 400 | run, LLM |
| `/stream`, `/chat/context` query | `_session_id_param` | 400 | approvals, run, LLM |
| `/sessions/{id}` | `SessionStore._path` + handler global | 400 | disco |

## Compatibilidade

- POSIX: os nomes de arquivo das sessões existentes não mudam.
- Windows: uma sessão antiga com `:` no nome nunca funcionou como arquivo
  (stream NTFS). As novas usam `%3A`.
- As permissões dos stores JSON passam a 0600 (vêm do `mkstemp`), como os
  JSONL de sessão já eram.
