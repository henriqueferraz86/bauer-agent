# ARCHITECTURE — Sprint 43

## `send_text` não dá sinal de sucesso/falha — resolver isto primeiro

`TelegramBridge.send_text(chat_id, text) -> None`
(`bauer/telegram_bridge.py:495`) engole TODA exceção internamente (cai
pra texto puro se o parse HTML falhar; se isso também falhar, só grava
`self.last_error` e loga — nunca propaga, nunca devolve um booleano).
Pra sprint 43 ter retry com distinção transitório/permanente, você
precisa de UM dos dois:

- (a) Adicionar um método novo e pequeno,
  `send_text_result(chat_id, text) -> bool` (ou nome melhor), que faz a
  mesma coisa mas devolve `True`/`False` e deixa a exceção subir na
  última tentativa — reaproveitando `chunk_text_fenced`/
  `md_to_telegram_html`, só mudando o tratamento de erro. Preferir isto:
  não reescreve `send_text` (usado em outros lugares), não quebra nada
  existente.
- (b) Comparar `self.last_error` antes/depois da chamada — mais frágil
  (race se duas entregas acontecerem "ao mesmo tempo", mesmo sendo raro
  num processo único). Evitar se (a) for viável.

Decida ao implementar; documente a escolha no BACKLOG.

## Registro de canais de entrega

Novo em `bauer/channel_execution.py` (módulo já existe, é onde o plano
063 já sugeria isto):

```python
_delivery_channels: dict[str, Callable[[str, str], bool]] = {}

def register_delivery_channel(prefix: str, deliver_fn: Callable[[str, str], bool]) -> None:
    _delivery_channels[prefix] = deliver_fn

def deliver_to_session(session_id: str, text: str) -> bool:
    """Ponto único de entrega — resolve o canal pelo prefixo do session_id."""
    for prefix, fn in _delivery_channels.items():
        if session_id.startswith(prefix):
            return fn(session_id, text)
    return False  # nenhum canal registrado para este session_id — não é erro, é no-op
```

`TelegramBridge` (onde for construído/inicializado — procure o
`__init__`/factory que já monta o bridge pro gateway, `telegram_bridge.py`
ou `channel_base.py`) chama
`register_delivery_channel("tg:", self._deliver_background_result)`, onde
`_deliver_background_result(session_id, text)` extrai `chat_id =
session_id.removeprefix("tg:")` e chama `send_text`/o método novo do
item anterior.

Comece SEM a heurística de "editar mensagem viva em lugar" — o plano 063
descreve isso como opcional/preferível, mas implementá-lo exige o bridge
saber rastrear "mensagem X ainda está editável pra esta sessão", que não
existe hoje. Mensagem nova sempre, com cabeçalho explícito, é o
comportamento padrão aceitável — não invente um mecanismo de "mensagem
viva" que não tem onde se apoiar.

## Captura de origem + watcher em `execution.py`

Em `_run_command`'s branch `background`
(`bauer/tools/execution.py:242-282`), logo após montar
`self._processes[pid_str] = {...}`:

```python
from ..runtime_ids import get_runtime_ids
runtime = get_runtime_ids()
origin_session_id = runtime[0] if runtime else None
self._processes[pid_str]["origin_session_id"] = origin_session_id

if origin_session_id is not None:
    def _watch_and_deliver():
        proc.wait()
        _deliver_background_completion(pid_str, cmd_str, proc, origin_session_id,
                                        stdout_buf, stderr_buf)
    _th.Thread(target=_watch_and_deliver, daemon=True).start()
```

`_deliver_background_completion` (função nova, mesmo módulo ou
`channel_execution.py` — decidir pelo acoplamento: se precisar só de
`deliver_to_session` + formatar texto, fica melhor em
`channel_execution.py` ao lado do registro; se precisar manipular
`self._processes`/estado do `ExecToolsMixin`, fica em `execution.py`):

1. Monta o texto (cabeçalho + comando truncado + exit code + stdout/
   stderr truncado — ver template no SPEC.md).
2. Checa idempotência (ver próxima seção) ANTES de chamar
   `deliver_to_session`.
3. Chama `deliver_to_session(origin_session_id, texto)` com retry
   `(2.0, 5.0, 10.0)` segundos se devolver `False`/levantar exceção
   transitória — classificar erro por heurística simples (timeout de
   rede, status HTTP 429/5xx se o erro carregar isso) vs. permanente
   (chat not found, bot blocked, token inválido — strings reconhecíveis
   no erro da API do Telegram).

## Idempotência

Um dict/set em memória simples, no mesmo módulo do registro de entrega
(`channel_execution.py`), chave = `f"{pid}:{started_at}"` (ou um hash
curto do comando + `started_at` se dois PIDs puderem colidir depois de
reciclados pelo SO — `started_at` já existe implicitamente via
`time.monotonic()` se você guardar no dict de `self._processes`).
Marcar "em entrega"/"entregue" ANTES de disparar o retry loop, não depois
— evita corrida se por algum motivo o watcher rodar duas vezes (não
deveria, mas defensivo é barato aqui).

## Testes (hermético, sem rede real)

- Teste do registro: `register_delivery_channel`/`deliver_to_session`
  isolados, com um `deliver_fn` fake.
- Teste de captura de origem: monkeypatch `get_runtime_ids` pra devolver
  um `session_id` fixo, rode `run_command(background=true)` com um
  comando curto real (ex.: `python3 -c "import time; time.sleep(0.1)"`),
  espere o watcher terminar (`proc.wait()`/`thread.join()`), confirme que
  `deliver_to_session` foi chamado com o `session_id` certo e o texto
  esperado.
- Teste de idempotência: forçar `deliver_to_session` a ser chamado duas
  vezes pro mesmo processo, confirmar que só entrega uma vez.
- Teste de retry: `deliver_fn` fake que falha N vezes e depois sucede,
  confirmar que tenta de novo até o limite e para corretamente em erro
  permanente vs. continua em erro transitório.
- Teste de ponta a ponta mínimo: `run_command(background=true)` de
  verdade (subprocess real, comando curto) dentro de um turno de canal
  simulado (reaproveitar `backend_at`/`message` de
  `tests/test_channel_execution.py` ou `tests/test_channel_agno_turn.py`
  como template), com um `TelegramBridge` fake/stub registrado como canal
  de entrega, confirmando que a mensagem de conclusão chega no fake sem
  o usuário perguntar de novo.
