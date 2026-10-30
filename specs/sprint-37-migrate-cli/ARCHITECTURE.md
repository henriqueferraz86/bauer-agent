# ARCHITECTURE — Sprint 37

## Onde mexer

Tudo dentro de `run_agent_session` (`bauer/agent.py:2725`), no bloco
`if kernel is not None:` (começa por volta da linha 3882, onde
`_turn_executor` é definido hoje). `AgnoRuntimeAdapter`/`delta_stream.py`/
Kernel já estão prontos dos sprints 35-36.

CORREÇÃO (revisão independente, achado bloqueante nº1): a chave de
ativação original, `runtime.default_adapter`, já tem default `"agno"` no
schema e é o contrato de outros consumidores (scheduler, painel, `bauer
runtime`/`bauer kernel`) — reaproveitá-la faria a CLI inteira rodar via
Agno sem o operador pedir em instalações sem override. Por isso esta sprint
também muda `bauer/config_loader.py` (`RuntimeSection`), adicionando
`cli_default_adapter: str = "bauer_native"` como chave PRÓPRIA da CLI,
independente de `default_adapter`. A afirmação original "nenhuma mudança
fora deste arquivo" não se sustenta mais — esta é a única exceção.

## Resolver o executor

Logo antes de `_turn_executor` ser definido, resolver qual executor usar:

```python
def _resolve_cli_runtime_adapter() -> str:
    try:
        from .config_loader import load_config
        return str(getattr(load_config().runtime, "cli_default_adapter", "") or "").strip().lower()
    except Exception:
        return ""  # sem config legível, mantém bauer_native — nunca quebra o turno por isso

_cli_runtime_adapter = _resolve_cli_runtime_adapter()
_use_agno_executor = _cli_runtime_adapter == "agno"
```

Chamar isto UMA VEZ por turno (não por sessão) — o operador pode editar
`config.yaml` entre turnos no mesmo processo REPL, e `rebuild_client_fn`
já recarrega config em outros pontos deste mesmo arquivo; manter o mesmo
padrão de "relê a cada turno" em vez de cachear na entrada da sessão.

## Executor condicional

```python
with _quadro_do_turno(console) as _frame:
    if _frame is not None:
        _frame.set_hud(_hud_state_atual())
    if _use_agno_executor:
        from .delta_stream import reset_sink, set_sink
        _sink_token = set_sink(_streamer) if _streamer is not None else None
        try:
            _kout = kernel.execute(_KReq(
                task=user_input, session_id=session_id or "",
                agent_id="cli.agent", input={"endpoint": "cli.agent"},
            ), executor=None)
        finally:
            if _sink_token is not None:
                reset_sink(_sink_token)
    else:
        _kout = kernel.execute(_KReq(
            task=user_input, session_id=session_id or "",
            agent_id="cli.agent", input={"endpoint": "cli.agent"},
        ), executor=_turn_executor)
```

Problema real a resolver na implementação: `_streamer` (o
`ConsoleStreamRenderer`) só existe DENTRO de `_invoke_turn()` — que não
roda quando `executor=None` (o Kernel despacha pro `AgnoRuntimeAdapter`
diretamente, sem chamar `_invoke_turn`). Então o streamer para o caminho
Agno precisa ser criado ANTES do `kernel.execute()`, no escopo externo —
mover a chamada `_make_streamer(console)` pra fora de `_invoke_turn()`
quando `_use_agno_executor` for `True`, ou criar um segundo streamer local
só para este branch. Preferir a segunda opção (streamer local ao branch
Agno) — não reestruturar `_invoke_turn()`, que o caminho `bauer_native`
continua usando sem mudança.

`_captured`/`outcome` do branch `bauer_native` não existe no branch Agno
(não há `_turn_executor` chamado) — o branch Agno trata o resultado de
`kernel.execute()` diretamente: sucesso → imprime a resposta final
(reaproveitar `_print_assistant_response`); bloqueio de governança → mesmo
tratamento de `_blocked` que já existe hoje (`_kout.error`/
`policy_reason`/`waiting_approval`).

## Suíte hermética

Testar SÓ a decisão de roteamento, não o turno Agno real:

- `_resolve_cli_runtime_adapter()` lê `runtime.cli_default_adapter` de um
  config.yaml temporário — parametrizar `"agno"` vs `"bauer_native"` vs
  ausente/erro de leitura (fallback seguro = bauer_native).
- `run_agent_session` com `kernel` fake (stub que grava qual `executor` foi
  passado a `.execute()`) comprova: config `agno` → `executor=None`; config
  ausente/`bauer_native` → `executor=_turn_executor` (uma closure, não
  `None`). Não precisa exercitar o corpo do Agno de verdade — só a decisão
  de QUAL valor chega a `.execute()`.

## Validação manual (fora da suíte, registrar no BACKLOG)

Rodar `bauer agent` interativo duas vezes, config com
`runtime.cli_default_adapter: agno` e depois `bauer_native`, mesmo roteiro:
pergunta simples, uma pergunta que force tool call, Ctrl-C no meio de uma
resposta longa, uma tool que peça aprovação. Registrar o que aconteceu dos
dois lados — não presumir paridade, observar.
