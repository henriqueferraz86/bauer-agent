"""`/atividade` no Telegram (sprint 45, plano 061 Etapa 3).

Apresentação pura em cima do motor JÁ compartilhado com a web
(`bauer/server_activity.py:build_chat_activity`): comando, cartão em texto,
atualização da MESMA mensagem e botões "Atualizar"/"Cancelar tarefa". Nenhuma
lógica de projeção nova aqui — só consumo.

Isolado do resto do bridge (não dentro de `telegram_bridge.py`) pelo mesmo
motivo do sprint 44 (`telegram_loop.py`): `telegram_bridge.py` já é grande e
está sob edição concorrente por outra sessão nesta mesma janela (Etapa 4,
aprovação remota) — menos superfície de merge.

A thread observadora faz POLLING, não `EventBus.subscribe()`: `publish()`
chama handlers inline, na mesma thread de quem publicou (o worker do turno/
Kernel) — um handler que trava numa chamada HTTP ao Telegram atrasaria a
governança. Poll é desacoplado, no próprio ritmo, igual ao que a web já faz
(SSE fazendo poll de `build_chat_activity`).
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .channel_base import ChannelMessage, chunk_text
from .channel_projects import private_telegram
from .core.runtime.run_manager import TERMINAL_RUN_STATUSES
from .server_activity import build_chat_activity
from .telegram_bridge import MAX_MESSAGE_CHARS, STREAM_EDIT_INTERVAL_S
from .usage_pricing import format_cost

logger = logging.getLogger("bauer.telegram_activity")

# Mesma cadência de throttle do _StreamingDraft (editMessageText custa rate
# limit do Telegram) — não é um valor novo, é o mesmo STREAM_EDIT_INTERVAL_S.
ACTIVITY_POLL_INTERVAL_S = STREAM_EDIT_INTERVAL_S
# Teto de observação — só UX (evita thread daemon vivendo pra sempre), não é
# mecanismo de governança: a tarefa em si continua no Kernel independente
# deste teto e independente do Telegram estar no ar.
ACTIVITY_WATCH_TIMEOUT_S = 2 * 60 * 60
# Os botões do cartão só fazem sentido enquanto a observação roda; TTL igual.
ACTIVITY_CALLBACK_TTL_S = ACTIVITY_WATCH_TIMEOUT_S
_MAX_ACTIVITY_CALLBACKS = 500


def render_activity_card(envelope_run: dict | None, run: Any, *,
                         project_name: str | None = None) -> str:
    """Formata o cartão de atividade em texto legível (pt-BR).

    Pura: recebe o dict de UM run já projetado por `build_chat_activity`
    (nunca lê run_manager/event_bus direto — nada de I/O aqui) e o `Run`
    real só para o custo (`cost_estimate`/`usage_known`, que o envelope
    projetado não expõe — ver ARCHITECTURE.md). Nunca mostra "US$ 0,00"
    quando o custo é desconhecido, nunca inventa progresso/percentual.
    """
    if not envelope_run:
        return "Execução não encontrada; pode ter sido removida ou nunca existiu nesta conversa."
    agents = envelope_run.get("agents") or []
    parent = agents[0] if agents else {}
    lines: list[str] = []
    if project_name:
        lines.append(f"Projeto: {project_name}")
    lines.append(f"Tarefa: {envelope_run.get('task') or 'Tarefa não informada'}")
    lines.append(f"Estado: {parent.get('activity') or 'Estado não informado'}")
    lines.append("")
    for member in agents:
        name = member.get("name") or member.get("agent_id") or "agente"
        activity = member.get("activity") or "Estado não informado"
        line = f"{name} — {activity}"
        tool = member.get("tool_name")
        if tool:
            line += f" ({tool})"
        lines.append(line)
    lines.append("")
    lines.append(_cost_line(run))
    text = "\n".join(lines).strip()
    chunks = chunk_text(text, MAX_MESSAGE_CHARS)
    return chunks[0] if chunks else text


def _cost_line(run: Any) -> str:
    cost = getattr(run, "cost_estimate", None) if run is not None else None
    usage_known = getattr(run, "usage_known", None) if run is not None else None
    if usage_known and cost is not None:
        return f"Custo registrado: {format_cost(float(cost))}"
    return "Custo: desconhecido"


def _activity_keyboard(token: str, cancelable: bool) -> dict:
    row = [{"text": "Atualizar", "callback_data": f"act:refresh:{token}"}]
    if cancelable:
        row.append({"text": "Cancelar tarefa", "callback_data": f"act:cancel:{token}"})
    return {"inline_keyboard": [row]}


def _resolve_workspace(backend: Any, context: Any) -> tuple[Path, str | None]:
    """Workspace explícito para `_resolver_kernel` — nunca depende da
    ContextVar de projeto ativo (só válida durante `project_scope`, que
    comandos síncronos como `/cancelar`/`/atividade` não abrem)."""
    if context is not None:
        return Path(context.workspace), context.name
    if not backend.is_ready:
        backend.initialize()
    return Path(backend._router.workspace).resolve(), None


def send_activity_card(bridge: Any, msg: ChannelMessage) -> None:
    """Handler de `/atividade`: cartão da execução ativa/recente da sessão.

    Sem execução ativa/recente ou Kernel indisponível → mensagem amigável,
    sem erro, sem botões, sem thread observadora (requisito 2 do SPEC).

    Bug fix (revisão pós-sprint 45, requisito 6 do SPEC): um segundo
    `/atividade` para o MESMO chat enquanto a execução anterior ainda não é
    terminal NÃO manda uma segunda mensagem — reusa e edita o cartão já
    enviado ("uma mensagem por execução, sempre editada, nunca duplicada").
    Se for uma execução diferente (ou não havia observador vivo), manda um
    cartão novo; de qualquer forma, o observador anterior para este chat é
    sempre aposentado via epoch/generation (`bridge._activity_watch_state`)
    — ver `_watch_activity` — nunca fica fazendo sua própria edição final
    concorrente com a do cartão novo.
    """
    chat_id = msg.chat_id
    if not private_telegram(msg):
        bridge.send_text(chat_id, "Use /atividade em uma conversa privada autorizada com o bot.")
        return
    backend = bridge.backend
    try:
        context = backend.capture_project_context(msg)
    except ValueError:
        bridge.send_text(chat_id, "Projeto indisponível. Use /projetos para escolher novamente.")
        return

    try:
        workspace, project_name = _resolve_workspace(backend, context)
        session_id = backend.session_key(msg)
        kernel = backend._resolver_kernel(workspace=workspace)
    except Exception:
        logger.exception("Falha resolvendo Kernel para /atividade")
        bridge.send_text(chat_id, "Não consegui consultar a atividade agora; tente novamente.")
        return
    if kernel is None:
        bridge.send_text(
            chat_id,
            "Atividade indisponível: o Kernel de governança não está habilitado para este workspace.",
        )
        return

    envelope = build_chat_activity(kernel.runs, kernel.bus, session_id)
    runs = envelope.get("runs") or []
    if not runs:
        bridge.send_text(chat_id, "Nenhuma execução ativa ou recente nesta conversa.")
        return

    run_proj = runs[0]
    run = kernel.runs.get_run(run_proj["run_id"])
    text = render_activity_card(run_proj, run, project_name=project_name)
    terminal = run_proj["status"] in TERMINAL_RUN_STATUSES
    token = secrets.token_urlsafe(18)
    keyboard = None if terminal else _activity_keyboard(token, cancelable=True)

    reuse_message_id: int | None = None
    with bridge._activity_callback_lock:
        current_watch = bridge._activity_watch_state.get(chat_id)
        if (
            current_watch is not None
            and not terminal
            and current_watch.get("run_id") == run_proj["run_id"]
        ):
            reuse_message_id = current_watch.get("message_id")
        generation = (current_watch.get("generation") if current_watch else 0) + 1

    api_kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if keyboard is not None:
        api_kwargs["reply_markup"] = keyboard

    message_id: int | None
    if reuse_message_id is not None:
        try:
            bridge._api("editMessageText", message_id=reuse_message_id, **api_kwargs)
        except Exception:
            logger.exception("Falha reeditando cartão de atividade existente no Telegram")
            bridge.send_text(
                chat_id, "Não consegui atualizar o cartão de atividade agora; tente novamente."
            )
            return
        message_id = reuse_message_id
    else:
        try:
            result = bridge._api("sendMessage", **api_kwargs)
        except Exception:
            logger.exception("Falha enviando cartão de atividade no Telegram")
            bridge.send_text(chat_id, "Não consegui enviar o cartão de atividade agora; tente novamente.")
            return
        message_id = result.get("message_id")
        if terminal or message_id is None:
            return  # execução já terminal (ou sem message_id) — nada a observar

    now = time.monotonic()
    _register_activity_callback(
        bridge, token=token, owner=str(msg.user_id), chat_id=chat_id,
        message_id=message_id, run_id=run_proj["run_id"], session_id=session_id,
        workspace=str(workspace), project_name=project_name, now=now,
    )
    with bridge._activity_callback_lock:
        bridge._activity_watch_state[chat_id] = {
            "run_id": run_proj["run_id"], "message_id": message_id, "generation": generation,
        }
    threading.Thread(
        target=_watch_activity, daemon=True, name=f"activity-{chat_id}",
        kwargs=dict(
            bridge=bridge, chat_id=chat_id, message_id=message_id,
            session_id=session_id, run_id=run_proj["run_id"], workspace=str(workspace),
            project_name=project_name, last_rendered=text,
            deadline_monotonic=now + ACTIVITY_WATCH_TIMEOUT_S, generation=generation,
        ),
    ).start()


def _register_activity_callback(bridge: Any, *, token: str, owner: str, chat_id: str,
                                message_id: int, run_id: str, session_id: str,
                                workspace: str, project_name: str | None, now: float) -> None:
    with bridge._activity_callback_lock:
        # Um novo /atividade no MESMO chat invalida os botões do cartão
        # anterior — evita dois tokens vivos apontando pra mensagens
        # diferentes na mesma conversa (mesma regra de _send_project_picker).
        bridge._activity_callbacks = {
            key: value for key, value in bridge._activity_callbacks.items()
            if value["expires_at"] > now and value["chat_id"] != chat_id
        }
        bridge._activity_callbacks[token] = {
            "owner": owner, "chat_id": chat_id, "message_id": message_id,
            "run_id": run_id, "session_id": session_id, "workspace": workspace,
            "project_name": project_name, "expires_at": now + ACTIVITY_CALLBACK_TTL_S,
        }
        while len(bridge._activity_callbacks) > _MAX_ACTIVITY_CALLBACKS:
            bridge._activity_callbacks.pop(next(iter(bridge._activity_callbacks)))


def _activity_watch_superseded(bridge: Any, chat_id: str, generation: int) -> bool:
    """Epoch check: um `/atividade` mais novo para o MESMO chat já assumiu o
    cartão (bug fix pós-sprint 45, requisito 6 do SPEC)? Se sim, este
    observador deve se aposentar em silêncio — SEM edição final, pois ele
    não é mais "o" cartão vivo; a edição final é responsabilidade exclusiva
    do observador atual (o que tem a `generation` corrente)."""
    with bridge._activity_callback_lock:
        current = bridge._activity_watch_state.get(chat_id)
        return current is None or current.get("generation") != generation


def _clear_activity_watch_state(bridge: Any, chat_id: str, generation: int | None) -> None:
    """Limpa o registro de observador vivo deste chat, só se ainda for a
    MESMA generation que está encerrando (nunca apaga o registro de um
    observador mais novo que já o sucedeu)."""
    if generation is None:
        return
    with bridge._activity_callback_lock:
        current = bridge._activity_watch_state.get(chat_id)
        if current is not None and current.get("generation") == generation:
            bridge._activity_watch_state.pop(chat_id, None)


def _watch_activity(*, bridge: Any, chat_id: str, message_id: int, session_id: str,
                    run_id: str, workspace: str, project_name: str | None,
                    last_rendered: str, deadline_monotonic: float,
                    generation: int | None = None) -> None:
    """Thread observadora: poll a cada ~2,5s, edita só se o texto mudou.

    Requisito 7 do SPEC (o mais crítico desta sprint): uma falha de edição
    intermediária NUNCA propaga e NUNCA seta uma flag "desisti para sempre"
    (diferente de `_StreamingDraft`) — a tentativa final, em estado terminal
    ou ao bater o teto de 2h, é SEMPRE feita, lida com sua própria falha de
    forma independente do que aconteceu nos ticks anteriores.

    `generation` (bug fix pós-sprint 45): epoch capturado no spawn por
    `send_activity_card`. Se um `/atividade` mais novo para o MESMO chat já
    assumiu o cartão, este observador se encerra em silêncio (sem editar —
    não é mais o cartão vivo). `None` (só usado por chamadas diretas nos
    testes unitários do observador) desliga a checagem.

    Bug fix (revisão pós-sprint 45): a resolução do Kernel/projeção
    (`_resolver_kernel`, `get_run`, `build_chat_activity`) agora está
    coberta por um `try/except` próprio, igual ao que já protegia só as
    chamadas a `editMessageText`. Antes, se o Kernel ficasse indisponível
    (ou `_resolver_kernel`/`get_run`/`build_chat_activity` levantasse) DEPOIS
    do `/atividade` já estar observando, o laço simplesmente parava sem
    nenhuma tentativa de edição final — o cartão ficava congelado para
    sempre mostrando "em execução" com botões mortos. Agora esse caso cai no
    MESMO caminho de melhor esforço do estado terminal/teto: tenta a edição
    final (texto conhecido mais recente + teclado limpo), loga e encerra.
    """
    backend = bridge.backend
    workspace_path = Path(workspace)
    while True:
        time.sleep(ACTIVITY_POLL_INTERVAL_S)

        if generation is not None and _activity_watch_superseded(bridge, chat_id, generation):
            return  # cartão mais novo para este chat já assumiu — encerra sem editar

        resolution_failed = False
        try:
            kernel = backend._resolver_kernel(workspace=workspace_path)
            if kernel is None:
                raise RuntimeError("Kernel indisponível durante observação de atividade")

            run = kernel.runs.get_run(run_id)
            if run is None:
                _clear_activity_watch_state(bridge, chat_id, generation)
                return  # execução desapareceu — nada a observar

            envelope = build_chat_activity(kernel.runs, kernel.bus, session_id, run_id=run_id)
            runs = envelope.get("runs") or []
            proj = runs[0] if runs else None
            terminal = proj is None or proj["status"] in TERMINAL_RUN_STATUSES
            timed_out = time.monotonic() >= deadline_monotonic
            text = render_activity_card(proj, run, project_name=project_name)
        except Exception:
            logger.warning(
                "Observador de atividade: falha resolvendo Kernel/projeção; "
                "encerrando com tentativa de edição final",
                exc_info=True,
            )
            resolution_failed = True
            terminal = True
            timed_out = False
            text = last_rendered

        if terminal or timed_out:
            if resolution_failed:
                text += (
                    "\n\n(Acompanhamento interrompido: não foi possível consultar o estado da "
                    "tarefa; a tarefa pode continuar em segundo plano — use /atividade de novo "
                    "para abrir um novo acompanhamento.)"
                )
            elif timed_out and not terminal:
                text += (
                    "\n\n(Acompanhamento encerrado após 2h; a tarefa pode continuar em segundo "
                    "plano — use /atividade de novo para abrir um novo acompanhamento.)"
                )
            # Tentativa final INCONDICIONAL — try/except próprio, nunca herda
            # o estado de uma falha anterior (ver docstring da função).
            try:
                bridge._api(
                    "editMessageText", chat_id=chat_id, message_id=message_id,
                    text=text, reply_markup={"inline_keyboard": []},
                )
            except Exception:
                logger.warning("Observador de atividade: edição final falhou", exc_info=True)
            _clear_activity_watch_state(bridge, chat_id, generation)
            break

        if text != last_rendered:
            try:
                bridge._api("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
                last_rendered = text
            except Exception:
                # Best-effort: o próximo tick tenta de novo; nunca desiste.
                logger.debug(
                    "Observador de atividade: edição intermediária falhou; tentará de novo",
                    exc_info=True,
                )


def handle_activity_callback(bridge: Any, cq: dict) -> bool:
    """Dispatch de callback com prefixo ``act:`` — mesmo padrão de
    `_project_callback`: token opaco, TTL, ownership, `message_id` exato,
    consumo atômico. Sempre retorna True (equivalente a "tratado") mesmo em
    no-op — `_handle_callback` já respondeu o `answerCallbackQuery` antes de
    chamar aqui.
    """
    message = cq.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    owner = str((cq.get("from") or {}).get("id", ""))
    message_id = message.get("message_id")
    parts = str(cq.get("data", "")).split(":", 2)
    if len(parts) != 3:
        return True
    _, action, token = parts

    record: dict | None = None
    with bridge._activity_callback_lock:
        candidate = bridge._activity_callbacks.get(token)
        valid = (
            candidate is not None
            and candidate["expires_at"] > time.monotonic()
            and candidate["owner"] == owner
            and candidate["chat_id"] == chat_id
            and candidate["message_id"] == message_id
        )
        if valid:
            record = dict(candidate)
            if action == "cancel":
                # Consumo atômico: o botão "Cancelar tarefa" é one-shot.
                bridge._activity_callbacks.pop(token, None)
    if record is None:
        return True

    if action == "refresh":
        _refresh_activity_card(bridge, record)
    elif action == "cancel":
        _cancel_via_activity_button(bridge, record)
    return True


def _refresh_activity_card(bridge: Any, record: dict) -> None:
    backend = bridge.backend
    try:
        kernel = backend._resolver_kernel(workspace=Path(record["workspace"]))
    except Exception:
        logger.warning("Botão Atualizar: Kernel indisponível", exc_info=True)
        return
    if kernel is None:
        return
    run = kernel.runs.get_run(record["run_id"])
    envelope = build_chat_activity(kernel.runs, kernel.bus, record["session_id"], run_id=record["run_id"])
    runs = envelope.get("runs") or []
    proj = runs[0] if runs else None
    if proj is None or run is None:
        return
    text = render_activity_card(proj, run, project_name=record.get("project_name"))
    terminal = proj["status"] in TERMINAL_RUN_STATUSES
    kwargs: dict[str, Any] = {
        "chat_id": record["chat_id"], "message_id": record["message_id"], "text": text,
    }
    if terminal:
        kwargs["reply_markup"] = {"inline_keyboard": []}
    try:
        bridge._api("editMessageText", **kwargs)
    except Exception:
        logger.warning("Botão Atualizar: edição falhou", exc_info=True)


def _cancel_via_activity_button(bridge: Any, record: dict) -> None:
    msg = ChannelMessage(
        channel="telegram", user_id=record["owner"], chat_id=record["chat_id"],
        text="", raw={"message": {"chat": {"type": "private"}}},
    )
    response = bridge._cancel_run_or_loop(msg, record["run_id"])
    try:
        bridge._api(
            "editMessageText", chat_id=record["chat_id"], message_id=record["message_id"],
            text=response, reply_markup={"inline_keyboard": []},
        )
    except Exception:
        logger.warning("Botão Cancelar: edição de confirmação falhou", exc_info=True)
