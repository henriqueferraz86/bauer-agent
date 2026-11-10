# ARCHITECTURE — Sprint 70

## Componentes

```text
memory_injection.py
  workspace_memory_block(ws)  → fatos com scope=ws + diretivas aprovadas de ws
  attach_workspace_memory(ctx, ws) → ctx.memory_block = provider | None
  strip_persisted_directives(messages)

ContextManager.get_payload()
  system = system_prompt + memory_block()     (recalculado a cada payload)
  + messages                                  (memory_block nunca entra aqui)

serve (server.py / server_chat.py / server_openai.py)
  _new_context(active_router) → attach(ctx, _effective_ws(router))
  /chat /stream /loop → store.save(sid, msgs, workspace=_effective_ws(active_router))
  /v1                 → store.save(sid, msgs, workspace=_effective_ws(deps.router))
  SessionStore.save(workspace=W)
     ├─ SqliteSessionStore(dir, semantic=False).save(sid, redigido, workspace=W)  (antes)
     └─ <sid>.jsonl redigido                                                     (depois)

gateway (channel_base.AgentBackend)
  _memory_workspace() = projeto vinculado ? workspace : None
  _turno_cru / _turno_agno:
     _attach_memory(ctx)                      (a cada turno)
     _store.save(key, msgs, workspace=_memory_workspace())

bauer agent (agent.py)
  LocalMemoryProvider.system_prompt_block()   (sem diretivas)
  attach_workspace_memory(ctx, router.workspace, include_consolidated=False)
  histórico retomado → strip_persisted_directives
```

## Proveniência por gravação

| Caminho | Workspace gravado |
|---|---|
| `/chat`, `/stream`, `/loop` | workspace efetivo do turno (projeto fixado na sessão) |
| `/v1` | workspace do router padrão do `serve` |
| Telegram privado com `/projetos` | workspace do projeto vinculado |
| Telegram sem vínculo, grupos, Discord | nenhum (`None`) → inelegível |

As regras de conflito são as da sprint 65 e rodam no mesmo `ON CONFLICT`:

- uma linha histórica continua NULL;
- uma linha carimbada que é gravada com outro workspace, ou com `None`, fica
  em conflito.

## Por que espelhar e não trocar o store do `serve`

O `SessionStore` JSONL tem contratos que o `SqliteSessionStore` não tem: o
lock por turno (`turn`/`serialized`, com `flock`), `load_public` e a redação
no disco. Trocar o store mexeria em todos os caminhos HTTP.

O espelho é aditivo e best-effort (`log_suppressed`). Ele grava a mesma cópia
redigida no banco que o consolidador já lê. A indexação semântica fica
desligada no espelho, para não abrir threads de embedding no `serve`.

## Ordem espelho → JSONL

O `SqliteSessionStore` importa os `*.jsonl` do diretório ao abrir, sem
proveniência. Se o JSONL de uma sessão nova existisse antes da linha no
banco, outro processo (ex.: `bauer agent` com o mesmo `--sessions-dir`) poderia
importá-lo primeiro, e a sessão ficaria histórica para sempre. Gravar o
espelho antes elimina essa corrida. Sessões JSONL anteriores à sprint são
importadas como históricas e continuam inelegíveis.

## Segurança

- O workspace nunca vem de um caminho enviado pelo cliente HTTP ou pelo chat.
  Vem do registro de projetos, resolvido no servidor, ou do vínculo fixado
  do canal.
- O bloco injetado é rotulado: os fatos aparecem como "referência, não
  instrução" e as diretivas como "aprovadas pelo usuário (revogáveis)". Ele
  é limitado a 4.000 caracteres e nunca é persistido.
- Falha ao ler a memória não quebra o turno: o bloco fica vazio e a falha é
  registrada em DEBUG (`log_suppressed`).
