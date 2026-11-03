# BACKLOG — Sprint 39: OpenAI-compat e consolidar

- [x] SPEC/ARCHITECTURE escritos com evidência real (checkout `68c8030`),
  referenciando o diff do sprint 38 (`770fe08`) como template.
- [x] `OpenAIRouteDependencies` + wiring em `server.py` com
  `runtime_mode`/`run_agno_turn`.
- [x] Branch condicional em `execute_v1` (Agno vs nativo) +
  `ctx.add_assistant` no branch Agno. Confirmado lendo `run_one_turn`
  (`bauer/agent.py`): o caminho nativo já grava em `ctx.messages` dentro
  do próprio loop (`ctx.add_assistant(response)`), então só o branch
  Agno precisava do append extra — igual ao sprint 38.
- [x] `set_policy_approval_wait`/`reset_policy_approval_wait` instalado no
  turno (bug pré-existente independente do Agno, corrigido de passagem).
  Confirmado que `execute_completion` tem um único ponto de
  cost_token/ids_token (sem o branch legado sem Kernel que `/chat`
  tinha), então só precisou de uma instalação, não duas.
- [x] Teste real de aprovação assíncrona via `/v1/chat/completions` +
  Agno, reaproveitando fixtures de `tests/test_approval_chat_e2e.py`
  (`test_real_agno_openai_completion_approval_runs_each_command_once`,
  com o novo helper `start_openai_completion`).
- [x] `bauer doctor` expõe executor ativo (`bauer/cli.py::doctor`, linha
  "Executor (web):"). Lido estaticamente de
  `<cwd>/memory/runtime/serve-runtime-mode.json` (mesmo arquivo que
  `server.py::_set_runtime_mode` persiste) — best-effort documentado no
  próprio comentário do código: se `bauer serve` rodou com
  `--sessions-dir` customizado ou em outro diretório, a linha mostra o
  default sem conseguir detectar a divergência (não há servidor ativo
  para perguntar via `/api/runtime/mode`).
- [x] Nota no painel desktop sobre escopo do toggle
  (`desktop/src/screens/Runtime.tsx`): adicionado texto explicitando que
  o toggle vale para `/chat`, `/stream` e `/v1/chat/completions`, e que
  CLI e Telegram usam mecanismos próprios.
- [x] ADR curto sobre manter `bauer_native` como fallback permanente
  (`plans/adr/001-bauer-native-fallback.md`).
- [x] Gates: Ruff bloqueante/estilo, suíte completa.
