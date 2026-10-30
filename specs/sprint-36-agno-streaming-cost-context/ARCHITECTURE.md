# ARCHITECTURE — Sprint 36

## Streaming genérico

`stream_agent`/`stream_team` já constroem o dict `message.delta` com
`content`. No ponto exato onde esse dict é montado (hoje só `yield`ado),
chamar `from ..delta_stream import emit_delta` (import local, mesmo padrão
usado pelo resto do adapter para evitar import cíclico) e `emit_delta(str(content))`
antes do `yield`. `emit_delta` é um no-op seguro quando não há sink instalado
(`_sink.get() is None`) — nenhuma mudança de comportamento para quem não
instalou sink. O bridge do `/stream` continua projetando eventos estruturados,
mas ignora `message.delta`, pois o mesmo chunk já chegou pela fila do sink.

Não propagar `on_round`/`on_tool` nesta sprint: o requisito mínimo é texto
chegando ao sink; eventos de progresso adicionais (chamada de tool, nova
rodada de LLM) ficam para quando um canal realmente precisar deles — manter
o escopo no que o critério de aceite pede.

## Custo/uso

Dentro do loop de `stream_agent`, quando `agno_event == "RunCompleted"` e
`not is_member_event` (o run top-level, não um membro de Team — evita
reportar custo duplicado por membro quando o objetivo é o custo do turno
inteiro), ler `metrics = getattr(event, "metrics", None)`. Se presente:

```python
usage = {
    "input_tokens": getattr(metrics, "input_tokens", 0) or 0,
    "output_tokens": getattr(metrics, "output_tokens", 0) or 0,
    "cache_read_input_tokens": getattr(metrics, "cache_read_tokens", 0) or 0,
    "cache_creation_input_tokens": getattr(metrics, "cache_write_tokens", 0) or 0,
}
provider, model_name = self._effective_model_spec(request.get("agent_spec") or {})
from ..cost_meter import report_llm_cost
report_llm_cost(provider, model_name, usage if any(usage.values()) else None)
```

Passar `None` (não um dict vazio) quando todos os campos são zero, para
reaproveitar o branch de `report_llm_cost` que já notifica o sink sobre
"usage ausente" sem calcular custo — mesmo comportamento que `bauer_native`
tem hoje para uma call sem usage reportado pelo provider.

`_effective_model_spec` já existe e é usado por `_build_model` — reutilizar,
não duplicar a lógica de resolução de provider/model.

Isto roda uma vez por `stream_agent` (não por member de Team) — Team ainda
não agrega custo por membro nesta sprint; é extensão natural futura, fora do
critério de aceite atual.

## Decisão de contexto

Nenhuma mudança de código. A seção "Decisão necessária" do SPEC.md e a
atualização do plano 062 documentam a constatação e a recomendação.

## Harness de comparação

Não implementado nesta sprint. Registrado no BACKLOG como item explicitamente
não concluído, com a dependência declarada (aguarda decisão de contexto).
