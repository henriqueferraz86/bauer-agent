# ARCHITECTURE — Sprint 40

## Chave de ativação

`bauer/config_loader.py`, `RuntimeSection` (ao lado de `cli_default_adapter`,
que já existe desde o sprint 37 — mesmo padrão exato):

```python
telegram_default_adapter: str = "bauer_native"
```

## Resolver o executor em `channel_base.py`

Função módulo-level (mesmo padrão de `_resolve_cli_runtime_adapter` em
`bauer/agent.py`, sprint 37) ou método da classe — decidir ao implementar
o que for mais testável:

```python
def _resolve_telegram_runtime_adapter(self) -> str:
    try:
        from .config_loader import load_config
        cfg = load_config(self.config_path)
        return str(getattr(cfg.runtime, "telegram_default_adapter", "") or "").strip().lower()
    except Exception:
        return ""  # best-effort — nunca quebra o turno por isto
```

## Novo método `_turno_agno`

Mesma assinatura de `_turno_cru` (`channel_base.py:988`):
`(self, ctx, key, client, model, text, on_delta=None, *, metrics=None)`.
Construir o adapter combinando os dois templates já existentes no
arquivo/repo:

- Resolução de `cfg`/`adapter_cfg`/`runtime_root`: copiar de
  `_run_chat_team` (`channel_base.py:1035-1055`).
- Parâmetros do construtor (`tool_router`, possivelmente
  `activity_sink`/`event_sink`): copiar de `server.py::_run_agno_turn`
  (linhas ~799-835) — ESSE é o que passa `tool_router=active_router`,
  necessário para o canal oferecer as mesmas tools ao Agno que oferece ao
  nativo. Avalie se faz sentido um `activity_sink` aqui (o canal já tem
  algum equivalente de "publicar atividade do agente" pro
  Telegram/`/atividade` do plano 061? Procure por
  `_publish_agno_activity`-like antes de inventar um novo mecanismo; se
  não existir equivalente, pode deixar `activity_sink=None` — não é
  bloqueante para esta sprint, é só paridade de UX, não de correção).

Dentro de `_turno_agno`, instalar `delta_stream.set_sink(on_delta)` igual
`_turno_cru` já faz (mesmo bloco try/finally) — **mas cuidado**: se
`_turno_agno` for chamado de dentro do mesmo `_executar` que já tem essa
lógica hoje em `_turno_cru`, considere se vale extrair o
`set_sink`/`reset_sink` para fora dos dois métodos (no `_executar`) em vez
de duplicar — decisão de limpeza, não é obrigatória, mas evite duplicar
texto idêntico sem necessidade.

`ctx.add_assistant(...)` no final do branch Agno — mesma razão dos
sprints 38/39. Primeiro confirme lendo `run_one_turn`/
`run_one_turn_with_fallback` (já confirmado nos sprints anteriores que
SIM grava em `ctx.messages` internamente) que o branch nativo não precisa
disso — só documentar de novo aqui por rigor, não precisa reinvestigar do
zero.

## Onde decidir o branch

Dentro de `_executar` (`channel_base.py:915-963`), nos DOIS pontos onde
`self._turno_cru(...)` é chamado hoje (linha ~947, dentro do
`if control is None:`, e linha ~950, dentro do `with control.activate():`)
— resolver o adapter UMA VEZ no topo de `_executar` e usar a mesma decisão
nos dois pontos, chamando `self._turno_agno(...)` em vez de
`self._turno_cru(...)` quando aplicável. NÃO mexer na estrutura de
`control.activate()`/`checkpoint()`/`close()` em volta — isso é o
mecanismo de cancelamento do sprint 34/plano 061 e já deve funcionar com
o Agno por baixo (checkpoint_execution() já instrumentado no
AgnoRuntimeAdapter desde o sprint 35) — só precisa ser PROVADO por teste,
não reescrito.

## Testes — reaproveitar o padrão de `tests/test_channel_execution.py`

Esse arquivo já tem o padrão exato necessário:
`backend_at(tmp_path)` constrói um `AgentBackend` real com Kernel
governado; `start_blocked_turn` faz `monkeypatch.setattr(
"bauer.agent.run_one_turn_with_fallback", turn)` com uma função `turn`
controlada por `threading.Event` que permite pausar o turno no meio,
checar `current_control().cancel_event.is_set()` depois de chamar
`backend.cancel_channel_execution(...)`, e verificar custo/budget/status
do run/sessão desbloqueada ao final
(`test_cancel_waits_for_real_worker_and_preserves_all_cost`, linha 71).

Para o branch Agno, escreva um teste análogo que:
1. Configure `config.yaml` do `backend_at` com
   `runtime: {telegram_default_adapter: agno}`.
2. Em vez de `monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback",
   turn)`, monkeypatche o ponto de entrada que `_turno_agno` de fato chama
   (ex.: `AgnoRuntimeAdapter.run_agent` ou `.stream_agent`, dependendo do
   que você implementar) com um equivalente controlado por
   `threading.Event` — mesma ideia, adaptada ao Agno.
3. Comprove: `backend.cancel_channel_execution(...)` interrompe o turno
   Agno, a sessão desbloqueia, o run fica `cancelled` (não travado em
   `running` nem vira `completed` por engano), e um novo turno na mesma
   sessão funciona depois (não fica "preso" pelo cancelamento anterior).

Também um teste SEM cancelamento (caminho feliz): turno simples completa
via Agno pelo canal, com o SDK Agno real (mesmo padrão de
`tests/test_agno_runtime_adapter.py` — `TwoCommands`/modelo fake local,
sem provider).

## O que NÃO fazer

- Não editar `config.yaml` real da Beelink.
- Não adicionar `set_policy_approval_wait` — não é bug aqui, está fora de
  escopo (ver SPEC.md).
- Não tocar em `AgnoRuntimeAdapter`/`delta_stream.py`/`execution_control.py`
  — já prontos.
