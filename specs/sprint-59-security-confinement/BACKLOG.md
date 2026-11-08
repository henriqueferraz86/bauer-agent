# BACKLOG — Sprint 59: confinamento

## Tarefas

### 0. Reproduzir no código atual

Status: concluído

- WEB-01 reproduz com Kernel ligado (`200 answer-overwrite`, victim regravado).
- T04 reproduz no bridge e no outbox.
- T05 reproduz em skills (escrita externa) e cron (leitura externa). Em
  memory, o caso do symlink externo já era bloqueado desde `6f68035`, mas com
  checagem e escrita em passos separados (TOCTOU) e sem escrita atômica.
- `bauer_audit_tools_probe_extra.py` agora usa um caminho relativo ao próprio
  arquivo e continua depois dos casos corrigidos.

### 1. Validação única de session_id (WEB-01)

Status: concluído

- `bauer/session_ids.py`, `SessionStore._path`, `SqliteSessionStore`.
- Ingressos: `/chat`, `/loop`, `/v1` (corpo e header), `/stream`,
  `/chat/context`, `/sessions/*` (handler global → 400) e CLI
  `--session-id`. Os canais passam pelo store.
- Regressões: `test_web01_*` (IDs válidos/inválidos, separadores Windows e
  Unix, absolutos, symlink, persistência normal, HTTP com Kernel on/off antes
  do LLM).

### 2. Mídia de saída confinada (T04)

Status: concluído

- `send_message` e `social_post` passam por `_sandbox` + `is_file()`, e o
  path absoluto resolvido vai ao bridge, ao outbox e ao Postiz.
- A resposta do Telegram sem projeto fica confinada ao workspace do gateway.
- Regressões: `test_t04_*`.

### 3. Stores internos sem seguir symlink (T05)

Status: concluído

- `bauer/confined_files.py` (`O_NOFOLLOW`/`samestat`, `os.replace` atômico,
  recusa de hardlink).
- memory, skills e cronjob usam `load_internal_json`/`save_internal_json`.
  O JSONL de sessão usa o mesmo módulo.
- Regressões: `test_t05_*`, incluindo a corrida "symlink criado depois da
  checagem".

### 4. Gates e revisão

Status: concluído

- `uv sync --frozen --extra dev`: ok.
- `uv run pytest tests/ -q --tb=short`: exit 0.
- Ruff E9/F63/F7/F82 e E/F/W: ok.
- `uv run mypy bauer/`: "Success: no issues found".
- Autorrevisão de segurança do diff: nenhuma guarda relaxada. Os únicos
  testes antigos alterados codificavam mídia fora do workspace.

## Notas de entrega

- Sprint 61 (T01/T06/T08 e metadados) foi empilhada nesta branch.
- Em aberto, fora do confinamento: `SqliteSessionStore.delete` remove vetores
  pelo prefixo `"{sid}:"`, então apagar a sessão `tg` removeria vetores de
  `tg:123`. É funcional, de baixo risco, e fica para uma manutenção futura.
- Risco residual T04 documentado na SPEC: a janela entre validação e abertura
  pelo bridge exige escrita concorrente dentro do workspace.
