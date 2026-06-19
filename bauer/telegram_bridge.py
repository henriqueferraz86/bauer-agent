"""Telegram Bridge — canal Telegram do Bauer Gateway.

Bot conversacional via Telegram Bot API com long-polling (getUpdates),
sem dependência de python-telegram-bot — só httpx, que já é core.

Feito para "rodar liso" (paridade com Hermes Agent):
- Typing heartbeat contínuo enquanto o agente trabalha
- Voice notes transcritas automaticamente (Groq/OpenAI Whisper)
- Fotos e documentos salvos no workspace e visíveis ao agente
- Mídia outbound: o agente envia fotos/arquivos/áudio de volta
- /model com teclado inline (botões) navegável
- Resposta em streaming: a mensagem cresce conforme o modelo gera
- Mensagens processadas em threads — polling nunca trava
- Retry com backoff + tratamento de 429 do Telegram

Setup::

    bauer gateway init          # wizard: token, allowlist, .env
    bauer telegram start        # só este canal
    bauer gateway start         # todos os canais habilitados

Segurança: allowlist vazia NEGA todo mundo (allow_all: true para liberar,
não recomendado). Offset persistido em workspace/.bauer_gateway/ — restart
não reprocessa mensagens antigas.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .channel_base import (
    AgentBackend,
    BaseBridge,
    ChannelMessage,
    RateLimiter,
    chunk_text,
    resolve_token,
)

logger = logging.getLogger("bauer.telegram")

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
MAX_CAPTION_CHARS = 1024
POLL_TIMEOUT_S = 30          # long-polling do getUpdates
TYPING_INTERVAL_S = 4.5      # sendChatAction expira em ~5s
STREAM_EDIT_INTERVAL_S = 2.5  # rate limit de editMessageText
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # limite da Bot API p/ getFile
MAX_TURN_WORKERS = 4         # turnos concorrentes (chats diferentes)

# Menu "/" do Telegram (setMyCommands) — igual Hermes/OpenClaw: ao digitar /
# o cliente mostra estas opções com descrição. Mantenha em sincronia com os
# handlers de AgentBackend.process() e com o HELP_TEXT do channel_base.
BOT_COMMANDS = [
    {"command": "start", "description": "Menu inicial"},
    {"command": "help", "description": "Ajuda e comandos disponíveis"},
    {"command": "status", "description": "Modelo, contexto e sessão atual"},
    {"command": "model", "description": "Trocar provider/modelo (botões)"},
    {"command": "tasks", "description": "Tarefas do kanban do workspace"},
    {"command": "new", "description": "Conversa nova (apaga o histórico)"},
    {"command": "clear", "description": "O mesmo que /new"},
]

# ── Mídia: classificação por extensão ────────────────────────────────────────

PHOTO_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VOICE_EXT = {".ogg", ".oga", ".opus"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}

# Marcador explícito que o agente pode emitir para mandar um arquivo:
#   [media: workspace/out/grafico.png]  (aliases: image/file/arquivo/imagem)
_MEDIA_MARKER_RE = re.compile(
    r"^[ \t]*\[(?:media|image|file|arquivo|imagem)\s*:\s*([^\]\n]+)\][ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def md_to_telegram_html(text: str) -> str:
    """Converte markdown comum do modelo para HTML do Telegram.

    O Telegram não renderiza markdown cru — ``**negrito**`` chega como
    asteriscos literais. Suporta: ```blocos```, `inline`, **negrito**,
    *itálico*, [link](url). Conteúdo é escapado antes (sem injeção de HTML).
    """
    parts = re.split(r"```(?:\w*\n)?(.*?)```", text or "", flags=re.DOTALL)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # conteúdo de bloco de código — só escapa
            out.append(f"<pre>{_html.escape(part.rstrip())}</pre>")
            continue
        seg = _html.escape(part)
        seg = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", seg)
        seg = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", seg)
        seg = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", seg)
        seg = re.sub(
            r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', seg
        )
        out.append(seg)
    return "".join(out)


def chunk_text_fenced(text: str, limit: int) -> list[str]:
    """chunk_text que não corta blocos ``` ao meio.

    Quando o ponto de corte cai dentro de um fence aberto, fecha o bloco no
    fim do chunk e reabre no início do próximo — cada pedaço renderiza
    corretamente no Telegram.
    """
    chunks = chunk_text(text, limit - 8)  # folga p/ os ``` injetados
    result: list[str] = []
    open_fence = False
    for chunk in chunks:
        if open_fence:
            chunk = "```\n" + chunk
        # fences neste chunk (contagem ímpar = terminou aberto)
        open_fence = (chunk.count("```") % 2) == 1
        if open_fence:
            chunk = chunk + "\n```"
        result.append(chunk)
    return result


def extract_outbound_media(text: str, workspace: Path | None = None) -> tuple[list[Path], str]:
    """Extrai arquivos que o agente quer mandar no chat.

    Reconhece marcadores ``[media: path]`` e linhas que são apenas um path
    existente com extensão de mídia. Retorna (paths existentes, texto limpo).
    """
    if not text:
        return [], text
    found: list[Path] = []

    def _resolve(raw: str) -> Path | None:
        raw = raw.strip().strip("'\"")
        if not raw:
            return None
        p = Path(raw)
        candidates = [p]
        if not p.is_absolute() and workspace is not None:
            candidates.append(workspace / p)
        for cand in candidates:
            try:
                if cand.is_file():
                    return cand.resolve()
            except OSError:
                continue
        return None

    def _on_marker(m: re.Match) -> str:
        path = _resolve(m.group(1))
        if path is not None:
            found.append(path)
            return ""
        return m.group(0)  # path não existe — deixa o texto como está

    cleaned = _MEDIA_MARKER_RE.sub(_on_marker, text)

    # Linhas que são só um path de mídia existente (fora de code fences)
    media_ext = PHOTO_EXT | VOICE_EXT | AUDIO_EXT | VIDEO_EXT
    lines = cleaned.split("\n")
    kept: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            kept.append(line)
            continue
        stripped = line.strip().strip("'\"")
        if (
            not in_fence
            and stripped
            and " " not in stripped
            and Path(stripped).suffix.lower() in media_ext
        ):
            path = _resolve(stripped)
            if path is not None and path not in found:
                found.append(path)
                continue
        kept.append(line)
    cleaned = "\n".join(kept)
    # dedup preservando ordem
    seen: set[Path] = set()
    unique = [p for p in found if not (p in seen or seen.add(p))]
    return unique, cleaned.strip()


class _TypingHeartbeat:
    """Mantém o "digitando…" vivo até a resposta sair (expira a cada ~5s).

    Uso: ``with _TypingHeartbeat(bridge, chat_id): ...`` — padrão Hermes
    (_keep_typing). Sem ele o usuário fica 30-60s olhando para o nada.
    """

    def __init__(self, bridge: "TelegramBridge", chat_id: str,
                 interval: float = TYPING_INTERVAL_S) -> None:
        self._bridge = bridge
        self._chat_id = chat_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_TypingHeartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"typing-{self._chat_id}")
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._bridge._send_typing(self._chat_id)
            self._stop.wait(self._interval)


class _StreamingDraft:
    """Resposta em streaming: envia uma mensagem e edita conforme chega texto.

    Transport "edit" do Hermes (stream_consumer.py): universalmente suportado.
    Edições parciais em texto puro (markdown incompleto quebraria o parse);
    a edição final aplica HTML formatado. Qualquer falha desativa o streaming
    silenciosamente — a resposta completa chega pelo caminho normal.
    """

    CURSOR = " ▌"

    def __init__(self, bridge: "TelegramBridge", chat_id: str,
                 interval: float = STREAM_EDIT_INTERVAL_S) -> None:
        self._bridge = bridge
        self._chat_id = chat_id
        self._interval = interval
        self._buffer: list[str] = []
        self._msg_id: int | None = None
        self._last_edit = 0.0
        self._dead = False
        self._status_mode = False  # True = draft mostra "🔧 executando…"
        # Tool Bridge: a "resposta" pode ser JSON de tool call — segura a
        # exibição até dar para distinguir texto de JSON (rodada silenciosa).
        self._silent_round = False
        self._display_decided = False
        self._lock = threading.Lock()

    # Chamado da thread do turno (sync), via delta sink
    def on_delta(self, chunk: str) -> None:
        if self._dead or not chunk:
            return
        with self._lock:
            self._buffer.append(chunk)
            text = "".join(self._buffer).strip()
            if not text:
                return
            if self._silent_round:
                return
            if not self._display_decided:
                if len(text) < 24:
                    return  # espera contexto suficiente p/ decidir
                if text.lstrip().startswith("{") or '"action"' in text[:60]:
                    self._silent_round = True  # parece tool JSON — não exibir
                    return
                self._display_decided = True
            now = time.monotonic()
            try:
                if self._msg_id is None:
                    result = self._bridge._api(
                        "sendMessage", chat_id=self._chat_id,
                        text=text[:MAX_MESSAGE_CHARS - 4] + self.CURSOR,
                    )
                    self._msg_id = int(result["message_id"])
                    self._last_edit = now
                elif self._status_mode or now - self._last_edit >= self._interval:
                    # status_mode: primeiro token substitui o "🔧 executando…"
                    self._bridge._api(
                        "editMessageText", chat_id=self._chat_id,
                        message_id=self._msg_id,
                        text=text[:MAX_MESSAGE_CHARS - 4] + self.CURSOR,
                    )
                    self._last_edit = now
                self._status_mode = False
            except Exception as exc:  # noqa: BLE001 — streaming é best-effort
                logger.debug("Streaming draft desativado: %s", exc)
                self._dead = True

    def on_round(self) -> None:
        """Nova rodada de LLM (após tools): finaliza o segmento atual."""
        with self._lock:
            if self._dead:
                return
            if self._msg_id is not None and self._buffer and self._display_decided:
                self._finalize_segment("".join(self._buffer).strip())
                self._msg_id = None
            self._buffer = []
            self._status_mode = False
            self._silent_round = False
            self._display_decided = False

    def on_tool(self, name: str) -> None:
        """Tool prestes a executar — mostra progresso no draft.

        Native tool calling não streama tokens; este sinal é o que dá vida
        à conversa: a mensagem aparece como "🔧 executando: shell…" e vai
        trocando conforme as tools rodam, até virar a resposta final.
        """
        if self._dead:
            return
        with self._lock:
            status = f"🔧 executando: {name}…"
            try:
                if self._msg_id is not None and self._buffer:
                    # havia texto streamado — finaliza e abre nova mensagem
                    self._finalize_segment("".join(self._buffer).strip())
                    self._buffer = []
                    self._msg_id = None
                if self._msg_id is None:
                    result = self._bridge._api(
                        "sendMessage", chat_id=self._chat_id, text=status,
                    )
                    self._msg_id = int(result["message_id"])
                else:
                    self._bridge._api(
                        "editMessageText", chat_id=self._chat_id,
                        message_id=self._msg_id, text=status,
                    )
                self._status_mode = True
                self._last_edit = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Tool progress desativado: %s", exc)
                self._dead = True

    def _finalize_segment(self, text: str) -> None:
        if not text or self._msg_id is None:
            return
        try:
            self._bridge._api(
                "editMessageText", chat_id=self._chat_id,
                message_id=self._msg_id,
                text=md_to_telegram_html(text[:MAX_MESSAGE_CHARS - 16]),
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            try:
                self._bridge._api(
                    "editMessageText", chat_id=self._chat_id,
                    message_id=self._msg_id, text=text[:MAX_MESSAGE_CHARS],
                )
            except Exception:  # noqa: BLE001
                pass

    def finish(self, final_text: str) -> bool:
        """Edição final formatada. True = entregue via streaming (não reenviar)."""
        with self._lock:
            if self._dead or self._msg_id is None:
                return False
            text = (final_text or "").strip()
            if not text:
                # nada para mostrar — apaga o draft órfão
                try:
                    self._bridge._api("deleteMessage", chat_id=self._chat_id,
                                      message_id=self._msg_id)
                except Exception:  # noqa: BLE001
                    pass
                return True
            chunks = chunk_text_fenced(text, MAX_MESSAGE_CHARS)
            self._finalize_segment(chunks[0])
            for extra in chunks[1:]:
                self._bridge.send_text(self._chat_id, extra)
            return True


class TelegramBridge(BaseBridge):
    """Canal Telegram via long-polling — implementação de BaseBridge."""

    name = "telegram"

    def __init__(
        self,
        token: str,
        backend: AgentBackend,
        allowed_users: list[int] | None = None,
        allow_all: bool = False,
        poll_interval: float = 2.0,
        max_msgs_per_minute: int = 20,
        state_dir: str | Path = "workspace/.bauer_gateway",
        model_allowlist: list[str] | None = None,
    ) -> None:
        super().__init__(backend, RateLimiter(max_msgs_per_minute))
        self.token = token
        self.allowed_users = {int(u) for u in (allowed_users or [])}
        self.allow_all = allow_all
        self.poll_interval = poll_interval
        self.state_dir = Path(state_dir)
        self.media_dir = self.state_dir / "media"
        self._offset_path = self.state_dir / "telegram_offset.json"
        self._offset = self._load_offset()
        self._http = httpx.Client(timeout=POLL_TIMEOUT_S + 10)
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_TURN_WORKERS, thread_name_prefix="tg-turn"
        )
        # Estado do model picker por chat: {"providers": [...], "models": {...}}
        self._picker_state: dict[str, dict] = {}
        # Filtro de modelos visíveis no /model — lista vazia = sem filtro
        self._model_allowlist: list[str] = list(model_allowlist or [])

    # ── API Telegram ───────────────────────────────────────────────────────

    def _api(self, method: str, _retries: int = 3, **params) -> dict:
        """POST num método da Bot API com retry/backoff; retorna o `result`.

        429 respeita ``retry_after`` do Telegram; erros de rede e 5xx fazem
        backoff 0.5/1.5/3s. 409 (outro consumidor) falha rápido — retry só
        pioraria o conflito.
        """
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        delays = [0.5, 1.5, 3.0]
        last_exc: Exception | None = None
        for attempt in range(max(1, _retries)):
            try:
                resp = self._http.post(url, json=params)
                if resp.status_code == 409:
                    # Dois processos consumindo o MESMO bot: o Telegram só
                    # permite um getUpdates por token.
                    raise RuntimeError(
                        "Telegram 409: outro processo já está consumindo este bot. "
                        "Pare-o com `bauer telegram stop` (ou mate o processo antigo) "
                        "e tente de novo."
                    )
                if resp.status_code == 429:
                    body = {}
                    try:
                        body = resp.json()
                    except Exception:  # noqa: BLE001
                        pass
                    retry_after = float(
                        (body.get("parameters") or {}).get("retry_after", 2)
                    )
                    logger.warning("Telegram 429 em %s — aguardando %.0fs",
                                   method, retry_after)
                    time.sleep(min(retry_after, 30.0))
                    continue
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(
                        f"Telegram API {method}: {data.get('description')}"
                    )
                return data.get("result")
            except (httpx.HTTPError,) as exc:
                last_exc = exc
                if attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Telegram API {method}: esgotou tentativas")

    def get_me(self) -> dict:
        """Valida o token e retorna info do bot (usado pelo wizard/doctor)."""
        return self._api("getMe")

    def register_commands(self) -> None:
        """Registra o menu '/' do bot (setMyCommands). Falha não é fatal."""
        try:
            self._api("setMyCommands", commands=BOT_COMMANDS)
            logger.info("Menu de comandos registrado (%d comandos)", len(BOT_COMMANDS))
        except Exception as exc:  # noqa: BLE001 — menu é cosmético
            logger.warning("setMyCommands falhou: %s", exc)

    def send_text(self, chat_id: str, text: str) -> None:
        """Envia em HTML (markdown convertido); cai para texto puro se o
        Telegram rejeitar o parse — nunca perde a mensagem por formatação."""
        for chunk in chunk_text_fenced(text, MAX_MESSAGE_CHARS):
            try:
                self._api(
                    "sendMessage", chat_id=chat_id,
                    text=md_to_telegram_html(chunk), parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001 — fallback plain text
                try:
                    self._api("sendMessage", chat_id=chat_id, text=chunk)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"sendMessage: {exc}"
                    logger.error("Falha enviando para %s: %s", chat_id, exc)

    def send_media(self, chat_id: str, path: str | Path, caption: str = "") -> bool:
        """Envia um arquivo local: foto/voz/áudio/vídeo/documento por extensão."""
        p = Path(path)
        if not p.is_file():
            logger.warning("send_media: arquivo não existe: %s", p)
            return False
        ext = p.suffix.lower()
        method, field = "sendDocument", "document"
        if ext in PHOTO_EXT:
            method, field = "sendPhoto", "photo"
        elif ext in VOICE_EXT:
            method, field = "sendVoice", "voice"
        elif ext in AUDIO_EXT:
            method, field = "sendAudio", "audio"
        elif ext in VIDEO_EXT:
            method, field = "sendVideo", "video"
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:MAX_CAPTION_CHARS]
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        try:
            with p.open("rb") as fh:
                resp = self._http.post(
                    url, data=data, files={field: (p.name, fh)}, timeout=120,
                )
            ok = resp.status_code == 200 and resp.json().get("ok", False)
            if not ok:
                logger.warning("send_media %s falhou: %s", method, resp.text[:200])
            return ok
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{method}: {exc}"
            logger.error("Falha enviando mídia para %s: %s", chat_id, exc)
            return False

    def _send_typing(self, chat_id: str) -> None:
        try:
            self._api("sendChatAction", _retries=1, chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001 — typing é cosmético
            pass

    # ── Download de mídia inbound ──────────────────────────────────────────

    def _download_file(self, file_id: str, suggested_name: str = "") -> Path | None:
        """Baixa um arquivo do Telegram (getFile + download) para media_dir."""
        try:
            info = self._api("getFile", file_id=file_id)
            remote_path = info.get("file_path", "")
            size = int(info.get("file_size") or 0)
            if size > MAX_DOWNLOAD_BYTES:
                logger.warning("Arquivo de %d bytes excede limite — ignorado", size)
                return None
            url = f"{TELEGRAM_API}/file/bot{self.token}/{remote_path}"
            resp = self._http.get(url, timeout=120)
            resp.raise_for_status()
            self.media_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(remote_path).suffix or Path(suggested_name).suffix or ".bin"
            base = re.sub(r"[^\w.\-]", "_", Path(suggested_name).stem) or "file"
            dest = self.media_dir / f"{base}_{int(time.time())}{ext}"
            dest.write_bytes(resp.content)
            return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning("Download de mídia falhou: %s", exc)
            return None

    def _ingest_media(self, message: dict, chat_id: str) -> str | None:
        """Converte mídia inbound em texto para o agente. None = sem mídia."""
        caption = (message.get("caption") or "").strip()

        voice = message.get("voice") or message.get("audio")
        if voice:
            path = self._download_file(voice.get("file_id", ""), "voice.ogg")
            if path is None:
                self.send_text(chat_id, "⚠️ Não consegui baixar o áudio.")
                return None
            from .transcription import transcribe_audio
            result = transcribe_audio(path)
            if not result.get("success"):
                self.send_text(
                    chat_id, f"⚠️ Não consegui transcrever o áudio: {result.get('error')}"
                )
                return None
            transcript = result["transcript"]
            prefix = f"🎤 (voice note transcrita): {transcript}"
            return f"{prefix}\n{caption}" if caption else prefix

        photos = message.get("photo") or []
        if photos:
            largest = photos[-1]  # API ordena por tamanho crescente
            path = self._download_file(largest.get("file_id", ""), "photo.jpg")
            if path is None:
                self.send_text(chat_id, "⚠️ Não consegui baixar a imagem.")
                return None
            note = (
                f"[O usuário enviou uma imagem, salva em: {path}]\n"
                f"Use a tool vision_analyze com image='{path}' para ver o conteúdo."
            )
            return f"{caption}\n{note}" if caption else note

        document = message.get("document")
        if document:
            name = document.get("file_name") or "arquivo.bin"
            path = self._download_file(document.get("file_id", ""), name)
            if path is None:
                self.send_text(chat_id, "⚠️ Não consegui baixar o arquivo.")
                return None
            note = (
                f"[O usuário enviou o arquivo '{name}', salvo em: {path}]\n"
                f"Use read_file ou as tools de arquivo para trabalhar com ele."
            )
            return f"{caption}\n{note}" if caption else note

        return None

    # ── Offset (não reprocessar updates após restart) ──────────────────────

    def _load_offset(self) -> int:
        try:
            data = json.loads(self._offset_path.read_text(encoding="utf-8"))
            return int(data.get("offset", 0))
        except Exception:
            return 0

    def _save_offset(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(
                json.dumps({"offset": self._offset}), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha persistindo offset: %s", exc)

    # ── Auth ───────────────────────────────────────────────────────────────

    def _is_authorized(self, msg: ChannelMessage) -> bool:
        if self.allow_all:
            return True
        try:
            return int(msg.user_id) in self.allowed_users
        except (TypeError, ValueError):
            return False

    def _user_id_authorized(self, user_id) -> bool:
        if self.allow_all:
            return True
        try:
            return int(user_id) in self.allowed_users
        except (TypeError, ValueError):
            return False

    # ── Model picker (inline keyboard) ─────────────────────────────────────

    def _active_pair(self, session_key: str) -> tuple[str, str]:
        """(provider, modelo) ativos para a sessão — espelha _cmd_model."""
        b = self.backend
        if session_key in b._session_overrides:
            _, model, provider = b._session_overrides[session_key]
            return provider, model
        return b._provider, b._model_overrides.get(session_key, b._model_name)

    def _provider_keyboard(self, providers: list[str], active: str) -> dict:
        try:
            from .provider_profile import get_profile
        except Exception:  # noqa: BLE001
            def get_profile(_):  # type: ignore[misc]
                return None
        rows, row = [], []
        for i, p in enumerate(providers):
            profile = get_profile(p)
            label = p + (" 🆓" if profile and profile.is_free else "")
            if p == active:
                label = "✅ " + label
            row.append({"text": label, "callback_data": f"mp:p:{i}"})
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "✖ Fechar", "callback_data": "mp:close"}])
        return {"inline_keyboard": rows}

    def _model_keyboard(self, chat_id: str, prov_idx: int, page: int) -> tuple[str, dict]:
        state = self._picker_state.get(chat_id) or {}
        providers = state.get("providers") or []
        provider = providers[prov_idx]
        models = state.setdefault("models", {}).get(provider)
        if models is None:
            models = self.backend._models_for_provider(provider)
            if self._model_allowlist:
                models = [m for m in models if m in self._model_allowlist]
                if not models:
                    models = [m for m in self._model_allowlist]
            state["models"][provider] = models
        try:
            from .provider_profile import get_profile
            profile = get_profile(provider)
        except Exception:  # noqa: BLE001
            profile = None

        per_page = 8
        total_pages = max(1, (len(models) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        window = models[page * per_page:(page + 1) * per_page]

        active_provider, active_model = "", ""
        # chat_id == session key suffix: tg:<chat_id>
        active_provider, active_model = self._active_pair(f"tg:{chat_id}")

        rows = []
        for j, m in enumerate(window):
            label = m
            if profile and profile.is_model_free(m):
                label += " 🆓"
            if provider == active_provider and m == active_model:
                label = "✅ " + label
            rows.append([{
                "text": label[:60],
                "callback_data": f"mp:m:{prov_idx}:{page * per_page + j}",
            }])
        nav = []
        if total_pages > 1:
            nav = [
                {"text": "◀", "callback_data": f"mp:pg:{prov_idx}:{page - 1}"},
                {"text": f"{page + 1}/{total_pages}", "callback_data": "mp:noop"},
                {"text": "▶", "callback_data": f"mp:pg:{prov_idx}:{page + 1}"},
            ]
        if nav:
            rows.append(nav)
        rows.append([
            {"text": "⬅ Providers", "callback_data": "mp:back"},
            {"text": "✖ Fechar", "callback_data": "mp:close"},
        ])
        free_note = ""
        if models and profile and any(profile.is_model_free(m) for m in models):
            free_note = "\n🆓 = gratuito"
        if not models:
            text = (f"📦 {provider} — não consegui listar modelos.\n"
                    f"Use /model {provider} <nome-do-modelo>")
        else:
            text = f"📦 {provider} — escolha o modelo:{free_note}"
        return text, {"inline_keyboard": rows}

    def _send_model_picker(self, chat_id: str, session_key: str) -> None:
        providers = self.backend._configured_providers()
        active_provider, active_model = self._active_pair(session_key)
        self._picker_state[chat_id] = {"providers": providers, "models": {}}
        self._api(
            "sendMessage", chat_id=chat_id,
            text=(f"⚙️ Modelo ativo: {active_model} ({active_provider})\n\n"
                  f"Escolha o provider:"),
            reply_markup=self._provider_keyboard(providers, active_provider),
        )

    def _handle_callback(self, cq: dict) -> None:
        cq_id = cq.get("id", "")
        data = cq.get("data", "") or ""
        from_user = cq.get("from") or {}
        message = cq.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        msg_id = message.get("message_id")
        try:
            self._api("answerCallbackQuery", _retries=1, callback_query_id=cq_id)
        except Exception:  # noqa: BLE001 — ack é cosmético
            pass
        if not self._user_id_authorized(from_user.get("id")):
            return
        if not data.startswith("mp:") or not chat_id or msg_id is None:
            return

        state = self._picker_state.get(chat_id)
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        def _edit(text: str, keyboard: dict | None = None) -> None:
            kwargs: dict = {"chat_id": chat_id, "message_id": msg_id, "text": text}
            if keyboard is not None:
                kwargs["reply_markup"] = keyboard
            try:
                self._api("editMessageText", **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("editMessageText do picker falhou: %s", exc)

        if action == "close":
            self._picker_state.pop(chat_id, None)
            _edit("✔️ Seletor fechado.")
            return
        if action == "noop" or state is None:
            return
        if action == "back":
            providers = state.get("providers") or []
            active_provider, active_model = self._active_pair(f"tg:{chat_id}")
            _edit(
                f"⚙️ Modelo ativo: {active_model} ({active_provider})\n\nEscolha o provider:",
                self._provider_keyboard(providers, active_provider),
            )
            return
        if action == "p" and len(parts) >= 3:
            try:
                idx = int(parts[2])
                text, kb = self._model_keyboard(chat_id, idx, 0)
            except Exception as exc:  # noqa: BLE001
                _edit(f"⚠️ Erro listando modelos: {exc}")
                return
            _edit(text, kb)
            return
        if action == "pg" and len(parts) >= 4:
            try:
                idx, page = int(parts[2]), int(parts[3])
                text, kb = self._model_keyboard(chat_id, idx, page)
            except Exception as exc:  # noqa: BLE001
                _edit(f"⚠️ Erro paginando: {exc}")
                return
            _edit(text, kb)
            return
        if action == "m" and len(parts) >= 4:
            providers = state.get("providers") or []
            try:
                prov = providers[int(parts[2])]
                model = state["models"][prov][int(parts[3])]
            except (IndexError, KeyError, ValueError):
                _edit("⚠️ Seleção inválida — abra o /model de novo.")
                return
            fake = ChannelMessage(
                channel="telegram", user_id=str(from_user.get("id", "")),
                chat_id=chat_id, text="",
            )
            result = self.backend._cmd_model(fake, f"{prov} {model}")
            self._picker_state.pop(chat_id, None)
            _edit(re.sub(r"[*_`]", "", result))  # resposta usa *bold* de texto
            return

    # ── Loop principal ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Long-polling até stop(). Erros de rede são re-tentados com backoff."""
        if not self.token:
            raise RuntimeError(
                "Token do Telegram ausente. Defina TELEGRAM_BOT_TOKEN no .env "
                "ou rode `bauer gateway init`."
            )
        if not self.allowed_users and not self.allow_all:
            logger.warning(
                "telegram.allowed_users vazio — NENHUM usuário será atendido. "
                "Rode `bauer gateway init` ou defina allow_all: true (cuidado)."
            )
        me = self.get_me()
        logger.info("Telegram bridge online como @%s", me.get("username"))
        self.register_commands()  # menu "/" no cliente Telegram

        backoff = 2.0
        while not self.stopped:
            try:
                updates = self._api(
                    "getUpdates",
                    _retries=1,
                    offset=self._offset + 1,
                    timeout=POLL_TIMEOUT_S,
                    allowed_updates=["message", "callback_query"],
                )
                backoff = 2.0
                for update in updates or []:
                    self._offset = max(self._offset, int(update.get("update_id", 0)))
                    self._handle_update(update)
                if updates:
                    self._save_offset()
            except (httpx.HTTPError, RuntimeError) as exc:
                if self.stopped:
                    break
                self.last_error = str(exc)
                logger.warning("Polling falhou (%s) — retry em %.0fs", exc, backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        logger.info("Telegram bridge parado.")

    def _handle_update(self, update: dict) -> None:
        """Roteia o update. Turnos rodam no executor — polling nunca trava."""
        if update.get("callback_query"):
            cq = update["callback_query"]
            self._executor.submit(self._safe_callback, cq)
            return

        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        from_user = message.get("from") or {}
        if not chat_id:
            return
        has_media = bool(
            message.get("voice") or message.get("audio")
            or message.get("photo") or message.get("document")
        )
        text = message.get("text", "")
        if not text and not has_media:
            return

        msg = ChannelMessage(
            channel="telegram",
            user_id=str(from_user.get("id", "")),
            chat_id=chat_id,
            text=text,
            user_name=from_user.get("username", "") or from_user.get("first_name", ""),
            raw=update,
        )
        if not self._is_authorized(msg):
            self.msgs_dropped += 1
            logger.info("telegram: mensagem de usuário não autorizado %s descartada",
                        msg.user_id)
            return

        # /model sem argumentos → picker com botões (UX Hermes)
        if text.startswith("/"):
            head, *rest = text.split(None, 1)
            if head.split("@")[0].lower() == "/model" and not rest:
                try:
                    self._send_model_picker(chat_id, msg.session_key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Model picker falhou (%s) — fallback texto", exc)
                    response = self.handle_message(msg)
                    if response:
                        self.send_text(chat_id, response)
                return

        self._executor.submit(self._safe_process, msg, message)

    def _safe_callback(self, cq: dict) -> None:
        try:
            self._handle_callback(cq)
        except Exception as exc:  # noqa: BLE001
            logger.error("Erro no callback do picker: %s", exc)

    def _safe_process(self, msg: ChannelMessage, message: dict) -> None:
        try:
            self._process_message(msg, message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro processando mensagem de %s", msg.chat_id)
            try:
                self.send_text(msg.chat_id, f"❌ Erro interno: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _process_message(self, msg: ChannelMessage, message: dict) -> None:
        """Turno completo: mídia inbound → agente (com typing) → mídia outbound."""
        media_text = self._ingest_media(message, msg.chat_id)
        if media_text is not None:
            msg.text = f"{msg.text}\n{media_text}".strip() if msg.text else media_text
        elif not msg.text:
            return  # mídia não suportada (sticker etc.) e sem texto

        streamer = _StreamingDraft(self, msg.chat_id)
        with _TypingHeartbeat(self, msg.chat_id):
            response = self.handle_message(msg, on_delta=streamer)
        if not response:
            return
        media_files, clean = extract_outbound_media(
            response, workspace=Path(self.backend.config_path).resolve().parent
        )
        delivered = streamer.finish(clean)
        if clean and not delivered:
            self.send_text(msg.chat_id, clean)
        for path in media_files:
            self.send_media(msg.chat_id, path)

    def stop(self) -> None:
        super().stop()
        self._executor.shutdown(wait=False)
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


def build_bridge_from_config(cfg, backend: AgentBackend | None = None) -> TelegramBridge:
    """Monta o TelegramBridge a partir de um BauerConfig validado."""
    token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    workspace = Path(cfg.agent.workspace)
    return TelegramBridge(
        token=token,
        backend=backend or AgentBackend(),
        allowed_users=cfg.telegram.allowed_users,
        allow_all=cfg.telegram.allow_all,
        poll_interval=cfg.telegram.poll_interval,
        max_msgs_per_minute=cfg.telegram.max_msgs_per_minute,
        state_dir=workspace / ".bauer_gateway",
        model_allowlist=cfg.telegram.model_allowlist or [],
    )


def run_bridge(config_path: str | Path = "config.yaml") -> None:
    """Entry point standalone: python -m bauer.telegram_bridge."""
    from .config_loader import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config(config_path)
    bridge = build_bridge_from_config(cfg)
    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()


if __name__ == "__main__":
    run_bridge()
