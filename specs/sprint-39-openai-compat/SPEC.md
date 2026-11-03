# SPEC — Sprint 39: migrar OpenAI-compat e consolidar (plano 062)

## Evidência (checkout `68c8030`, branch `codex/sprint-39-openai-compat`)

Ao contrário de `/chat` e `/stream` (sprints 36/38 — já despachavam para
Agno via `_runtime_mode()` antes mesmo dessas sprints começarem),
`/v1/chat/completions` (`bauer/server_openai.py`) **genuinamente nunca
despachou para Agno**: `execute_v1` (dentro de `execute_completion`,
~linha 186) chama incondicionalmente `deps.run_one_turn_with_fallback(...)`
— zero ocorrência de `runtime_mode`/`run_agno_turn` no arquivo inteiro.
`OpenAIRouteDependencies` (~linha 42) não tem os campos `runtime_mode`/
`run_agno_turn` que `ChatRouteDependencies` já tem. Este é o primeiro
sprint do plano 062 onde o trabalho descrito no plano original ainda
corresponde à realidade do código.

Também confirmado: `execute_v1` **não** chama `set_policy_approval_wait`
(mesmo bug que o sprint 38 encontrou e corrigiu em `/chat`) — se uma tool
exigir aprovação aqui, hoje levanta `ToolError("waiting_approval: ...")`
na hora em vez de bloquear até o operador decidir. Isso já é um bug no
caminho `bauer_native` atual deste endpoint, não é específico do Agno —
mas corrigir agora evita herdar o mesmo defeito pro branch Agno que esta
sprint cria.

## Objetivo e escopo

1. Adicionar `runtime_mode: Callable[[], str]` e
   `run_agno_turn: Callable[..., dict[str, Any]]` a
   `OpenAIRouteDependencies`, passados em `bauer/server.py` na construção
   de `build_openai_router(OpenAIRouteDependencies(...))` — mesmos
   closures (`_runtime_mode`, `_run_agno_turn`) já usados para
   `ChatRouteDependencies` (linhas 1502-1503).
2. Em `execute_v1`, replicar o padrão corrigido de `server_chat.py`
   (`run_chat_turn`, sprint 38): `if deps.runtime_mode() == "agno":
   result = deps.run_agno_turn(...); response, tool_log = result[...]`
   senão o branch nativo atual.
3. **Aplicar desde já** os dois fixes que o sprint 38 precisou descobrir
   por teste: `set_policy_approval_wait(True)`/`reset_policy_approval_wait`
   em volta do turno (ambos os branches — o bug já existe mesmo sem Agno),
   e `ctx.add_assistant(...)` no branch Agno (o branch nativo aqui já
   grava via `run_one_turn_with_fallback`/`ctx.add_assistant` dentro do
   próprio loop — confirmar ao implementar, não presumir).
4. Teste real (SDK Agno real, sem provider, mesmo padrão de
   `tests/test_approval_chat_e2e.py`) cobrindo `/v1/chat/completions` com
   `runtime_mode=agno` + aprovação assíncrona via API — terceiro endpoint
   com essa cobertura, mesmo padrão de `/stream`/`/chat`.
5. `bauer doctor`: expor qual executor está ativo (useful mínimo: imprimir
   `runtime_mode()` atual e lembrar que é um único toggle, não por canal —
   não inventar granularidade que não existe ainda).
6. Painel desktop (`desktop/src/screens/Runtime.tsx`): já expõe
   `default_adapter`/`runtime_mode` (achado do sprint 38) — avaliar se
   vale adicionar uma nota explícita "aplica-se a /chat, /stream e agora
   /v1/chat/completions; CLI e Telegram usam mecanismos próprios" para não
   insinuar granularidade por canal que não existe. Mudança pequena de
   texto, não de mecanismo.
7. ADR curto (arquivo novo `plans/adr/001-bauer-native-fallback.md` ou
   seção dedicada neste SPEC, decidir ao implementar o que for mais simples
   de manter) sobre o destino do `bauer_native`: a recomendação, dado tudo
   observado nos sprints 35-38 (sem regressão, sem motivo técnico para
   remover, usado como fallback real e testado em paralelo o tempo todo),
   é **manter como fallback permanente configurável**, não planejar
   depreciação. Isto não é uma decisão nova inventada agora — é a
   consolidação do que já é verdade na prática desde o sprint 35.

## Fora de escopo

- Migrar Telegram (sprint 40).
- Granularidade de executor por canal no painel (não existe, não é desta
  sprint inventar).
- Qualquer mudança em `AgnoRuntimeAdapter`/`delta_stream.py`/Kernel.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy`

## Critérios de aceite

- `/v1/chat/completions` com `runtime_mode: agno` funciona (batch e
  stream=true) com paridade observável ao caminho nativo.
- Teste real comprova aprovação assíncrona funcionando neste endpoint com
  Agno ativo.
- `set_policy_approval_wait` corrigido nos dois branches (nativo e Agno) —
  bug pré-existente independente desta sprint, corrigido de passagem.
- `bauer doctor` expõe o executor ativo.
- ADR/decisão sobre `bauer_native` documentada.
- Suíte completa + Ruff bloqueante/estilo verdes; nenhuma regressão.

## STOP

Se a correção de `set_policy_approval_wait` neste endpoint (que já é um
bug pré-existente, não introduzido por esta sprint) revelar uma
diferença de comportamento maior que os clientes OpenAI-compat (ex.:
Claw3D) dependem — parar e voltar ao operador antes de mudar
comportamento que clientes externos já tratam como contrato.
