"""Saída de voz da sessão Jarvis.

Esta primeira versão mantém o pipeline simples e confiável:

stream do LLM -> buffer de frases -> TTS local/remoto -> WAV -> playback

O módulo é deliberadamente independente da captura/STT. VAD e AEC entram quando
o barge-in opt-in é habilitado, com um controlador compartilhado por toda a
resposta para manter o full-duplex estável.
"""

from __future__ import annotations

import base64
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx

from .http_shared import shared_ssl_context
from .tts import _tts_provider_pref, synthesize_speech as synthesize_local_tts
from .voice_metrics import VoiceTurnMetrics
from .voice_text import strip_emoji_for_speech

logger = logging.getLogger("bauer.voice_session")

DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_TTS_VOICE = "alloy"
_VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
_VALID_MODELS = {"tts-1", "tts-1-hd"}
_SENTENCE_END = re.compile(r"(?<=[.!?])(?=\s|$)")


def configured_tts_voice(default: str = DEFAULT_TTS_VOICE) -> str:
    """Resolve a voz remota, incluindo o perfil grave inspirado em Jarvis."""
    configured = os.environ.get("BAUER_TTS_VOICE", "").strip()
    if configured:
        return configured
    if os.environ.get("BAUER_TTS_PROFILE", "").strip().lower() == "jarvis":
        return "onyx"
    return default


def configured_tts_rate() -> int:
    """Resolve a velocidade SAPI (-10..10); Jarvis usa ritmo mais pausado."""
    raw_rate = os.environ.get("BAUER_TTS_RATE", "").strip()
    if not raw_rate and os.environ.get("BAUER_TTS_PROFILE", "").strip().lower() == "jarvis":
        return -1
    if not raw_rate:
        return 0
    try:
        rate = int(raw_rate)
    except ValueError as exc:
        raise VoiceOutputError("BAUER_TTS_RATE deve ser um inteiro entre -10 e 10") from exc
    if not -10 <= rate <= 10:
        raise VoiceOutputError("BAUER_TTS_RATE deve estar entre -10 e 10")
    return rate


class VoiceOutputError(RuntimeError):
    """Falha recuperável ao gerar ou reproduzir uma resposta de voz."""


class VoiceOutputCancelled(VoiceOutputError):
    """Saída de voz interrompida pelo usuário."""


class VoiceTurnCancelled(VoiceOutputError):
    """Turno completo interrompido pelo usuário durante a resposta."""


class BargeInController:
    """Mantém VAD + AEC ativos durante todos os segmentos de uma resposta."""

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self._monitor: Any = None
        self._reference: Any = None
        self._playback_active = threading.Event()
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls, cancel_event: threading.Event | None = None) -> "BargeInController | None":
        enabled = os.environ.get("BAUER_VOICE_BARGE_IN", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not enabled:
            return None
        controller = cls(cancel_event)
        try:
            controller.start()
        except Exception as exc:  # noqa: BLE001 - barge-in é opcional
            logger.warning("barge-in não ativado: %s", exc)
            return None
        return controller

    def start(self) -> None:
        from .voice_aec import NLMSEchoCanceller
        from .voice_vad import MicrophoneVADMonitor

        self._monitor = MicrophoneVADMonitor(
            self._on_event,
            reference_provider=self.next_frame,
            echo_canceller=NLMSEchoCanceller(),
        )
        self._monitor.start()

    def stop(self) -> None:
        self._playback_active.clear()
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.stop()

    def prepare_playback(self, audio_file: str | Path) -> Callable[[], None]:
        from .voice_aec import PlaybackReference

        reference = PlaybackReference(audio_file)
        with self._lock:
            self._reference = reference

        def start_reference() -> None:
            reference.start()
            self._playback_active.set()

        return start_reference

    def end_playback(self) -> None:
        self._playback_active.clear()
        with self._lock:
            self._reference = None

    def next_frame(self, frame_count: int) -> Any:
        with self._lock:
            reference = self._reference
        if reference is not None:
            return reference.next_frame(frame_count)
        try:
            import numpy as np

            return np.zeros(max(0, int(frame_count)), dtype=np.float32)
        except ImportError:
            return []

    def _on_event(self, event: str) -> None:
        from .voice_vad import VOICE_STARTED

        if event == VOICE_STARTED and self._playback_active.is_set():
            self.cancel_event.set()


class StreamingVoiceOutput:
    """Converte a resposta final do stream em áudio numa fila dedicada.

    O produtor (stream do LLM) nunca espera a chamada TTS nem o playback. A
    fila mantém a ordem das frases e o worker encerra de forma determinística
    quando ``finish`` é chamado ao final do turno. O texto só é liberado para
    o TTS depois que o agente confirma que a rodada não gerou uma tool call;
    assim, preâmbulos ou JSON de ferramentas nunca são falados antes do
    resultado da execução.
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_TTS_MODEL,
        voice: str | None = None,
        metrics: VoiceTurnMetrics | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.voice = configured_tts_voice(voice or DEFAULT_TTS_VOICE)
        self.metrics = metrics or VoiceTurnMetrics()
        self._buffer = ""
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._cancel_event = threading.Event()
        self.barge_in = BargeInController.from_environment(self._cancel_event)
        self._worker = threading.Thread(
            target=self._consume,
            name="bauer-voice-output",
            daemon=True,
        )
        self._error: VoiceOutputError | None = None
        self._spoken_segments = 0
        self._finished = False
        self._final_answer_ready = False
        self.metrics_payload: dict[str, Any] | None = None
        self._worker.start()

    def on_delta(self, chunk: str) -> None:
        if self._finished or not chunk:
            return
        self.metrics.mark("llm_first_delta")
        self._buffer += chunk

    def on_round(self) -> None:
        # A rodada pode ser uma tool call ou uma resposta final. O caller
        # chama on_tool() ou on_final() depois de inspecionar a mensagem.
        # Portanto, não fale nada neste ponto.
        return

    def on_final(self) -> None:
        """Libera a rodada somente depois de confirmar que é a resposta final."""
        if self._finished:
            return
        self._final_answer_ready = True
        self._enqueue_ready_sentences()

    def on_tool(self, _name: str) -> None:
        # Todo texto acumulado pertence à solicitação de ferramenta (ou a um
        # preâmbulo que não deve ser falado). O resultado só será produzido na
        # próxima rodada do LLM, depois que a ferramenta terminar.
        self._final_answer_ready = False
        self._buffer = ""

    def finish(self) -> None:
        if self._finished:
            return
        if self._final_answer_ready:
            self._enqueue_buffer(force=True)
        else:
            # Não transforme uma rodada interrompida ou de tool call em fala.
            self._buffer = ""
        self._finished = True
        self._queue.put(None)
        try:
            self._worker.join(timeout=120)
        except KeyboardInterrupt:
            self.cancel()
            self._worker.join(timeout=2)
        if self._worker.is_alive():
            self._set_error("worker de playback não terminou no tempo esperado")
        self.metrics_payload = self.metrics.finish(
            status="error" if self._error is not None else "completed",
            error=str(self._error) if self._error is not None else None,
        )
        if self.barge_in is not None:
            self.barge_in.stop()

    def cancel(self) -> None:
        """Interrompe o segmento atual e descarta segmentos ainda não falados."""
        self._cancel_event.set()
        if self.barge_in is not None:
            self.barge_in.stop()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if not self._finished:
            self._finished = True
            self._queue.put(None)

    @property
    def error(self) -> VoiceOutputError | None:
        return self._error

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def spoken_segments(self) -> int:
        return self._spoken_segments

    def _enqueue_ready_sentences(self) -> None:
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if match is None:
                # Evita esperar indefinidamente por pontuação em respostas
                # longas, listas ou código: corta num espaço confortável.
                if len(self._buffer) < 280:
                    return
                cut = self._buffer.rfind(" ", 80, 280)
                if cut < 0:
                    return
                self._enqueue_buffer(end=cut, force=False)
                continue
            self._enqueue_buffer(end=match.end(), force=False)

    def _enqueue_buffer(self, *, end: int | None = None, force: bool) -> None:
        if not self._buffer.strip():
            self._buffer = ""
            return
        if end is None:
            if not force:
                return
            end = len(self._buffer)
        segment = self._buffer[:end].strip()
        self._buffer = self._buffer[end:].lstrip()
        if segment:
            self._queue.put(segment)

    def _consume(self) -> None:
        while True:
            segment = self._queue.get()
            if segment is None:
                return
            if self._error is not None:
                continue
            try:
                speak_response(
                    segment,
                    self.client,
                    model=self.model,
                    voice=self.voice,
                    cancel_event=self._cancel_event,
                    metrics=self.metrics,
                    barge_in=self.barge_in,
                )
                self._spoken_segments += 1
            except VoiceOutputCancelled:
                return
            except Exception as exc:  # noqa: BLE001 - voz é saída acessória
                self._set_error(str(exc))

    def _set_error(self, message: str) -> None:
        if self._error is None:
            self._error = VoiceOutputError(message)


class CombinedStreamSink:
    """Encaminha o protocolo de streaming para texto e voz em paralelo."""

    def __init__(self, text_sink: Any, voice_sink: StreamingVoiceOutput) -> None:
        self.text_sink = text_sink
        self.voice_sink = voice_sink

    @property
    def text(self) -> str:
        return str(getattr(self.text_sink, "text", ""))

    @property
    def escreveu_algo(self) -> bool:
        return bool(getattr(self.text_sink, "escreveu_algo", False))

    @property
    def diag(self) -> Any:
        return getattr(self.text_sink, "diag", None)

    @property
    def cancelled(self) -> bool:
        return bool(getattr(self.voice_sink, "cancelled", False))

    def on_delta(self, chunk: str) -> None:
        if self.text_sink is not None:
            self.text_sink.on_delta(chunk)
        self.voice_sink.on_delta(chunk)

    def on_round(self) -> None:
        if self.text_sink is not None:
            self.text_sink.on_round()
        self.voice_sink.on_round()

    def on_final(self) -> None:
        """Informa à voz que a rodada foi confirmada como resposta final."""
        self.voice_sink.on_final()

    def on_tool(self, name: str) -> None:
        if self.text_sink is not None:
            self.text_sink.on_tool(name)
        self.voice_sink.on_tool(name)

    def close(self) -> str:
        if self.text_sink is not None:
            return self.text_sink.close()
        return self.text


def _tts_url(client: Any) -> str:
    """Deriva o endpoint de TTS do cliente OpenAI-compatible do Bauer."""
    chat_url = getattr(client, "_chat_url", None)
    if callable(chat_url):
        url = str(chat_url())
        if url.endswith("/chat/completions"):
            return f"{url[:-len('/chat/completions')]}/audio/speech"

    host = str(getattr(client, "host", "")).rstrip("/")
    if not host:
        raise VoiceOutputError("cliente ativo não expõe um endpoint compatível com TTS")
    if host.endswith("/v1"):
        return f"{host}/audio/speech"
    return f"{host}/v1/audio/speech"


def _client_headers(client: Any) -> dict[str, str]:
    headers = getattr(client, "_headers", None)
    if not isinstance(headers, dict):
        raise VoiceOutputError("cliente ativo não expõe credenciais para TTS")
    return {str(key): str(value) for key, value in headers.items()}


def synthesize_speech(
    text: str,
    client: Any,
    output_file: str | Path,
    *,
    model: str = DEFAULT_TTS_MODEL,
    voice: str = DEFAULT_TTS_VOICE,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Gera um WAV através de um endpoint OpenAI-compatible."""
    clean_text = strip_emoji_for_speech(text)
    if not clean_text:
        raise VoiceOutputError("resposta vazia não pode ser convertida em voz")
    if cancel_event is not None and cancel_event.is_set():
        raise VoiceOutputCancelled("síntese interrompida")
    if len(clean_text) > 4096:
        clean_text = clean_text[:4096]
    if model not in _VALID_MODELS:
        raise VoiceOutputError(f"modelo TTS inválido: {model}")
    if voice not in _VALID_VOICES:
        raise VoiceOutputError(f"voz TTS inválida: {voice}")

    destination = Path(output_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = httpx.post(
        _tts_url(client),
        headers=_client_headers(client),
        json={
            "model": model,
            "voice": voice,
            "input": clean_text,
            "response_format": "wav",
        },
        timeout=120.0,
        verify=shared_ssl_context(),
    )
    if response.status_code != 200:
        detail = response.text[:300]
        raise VoiceOutputError(f"TTS retornou HTTP {response.status_code}: {detail}")
    if not response.content:
        raise VoiceOutputError("TTS retornou áudio vazio")
    if cancel_event is not None and cancel_event.is_set():
        raise VoiceOutputCancelled("síntese interrompida")
    destination.write_bytes(response.content)
    return destination


def play_audio(
    audio_file: str | Path,
    cancel_event: threading.Event | None = None,
    playback_start: Any = None,
) -> None:
    """Reproduz um WAV usando o player nativo ou um player disponível."""
    path = Path(audio_file)
    if not path.is_file():
        raise VoiceOutputError(f"arquivo de áudio não encontrado: {path}")
    if cancel_event is not None and cancel_event.is_set():
        raise VoiceOutputCancelled("playback interrompido")

    if os.name == "nt":
        try:
            import winsound

            sound_api: Any = winsound
            if cancel_event is None:
                if playback_start is not None:
                    playback_start()
                sound_api.PlaySound(str(path), sound_api.SND_FILENAME)
                return
            if playback_start is not None:
                playback_start()
            sound_api.PlaySound(
                str(path), sound_api.SND_FILENAME | sound_api.SND_ASYNC
            )
            try:
                with wave.open(str(path), "rb") as wav:
                    duration = wav.getnframes() / max(wav.getframerate(), 1)
                if cancel_event.wait(timeout=max(duration, 0.05) + 0.1):
                    raise VoiceOutputCancelled("playback interrompido")
            finally:
                sound_api.PlaySound(None, 0)
            return
        except VoiceOutputCancelled:
            raise
        except Exception as exc:  # pragma: no cover - depende do Windows/driver
            raise VoiceOutputError(f"playback do Windows falhou: {exc}") from exc

    # Permite usar a mesma sessão em Linux/macOS sem adicionar uma dependência
    # pesada ao pacote. Um player do sistema é suficiente para o MVP.
    for command in (("afplay",), ("aplay",), ("paplay",), ("ffplay", "-nodisp", "-autoexit")):
        executable = shutil.which(command[0])
        if executable is None:
            continue
        process = None
        try:
            if playback_start is not None:
                playback_start()
            process = subprocess.Popen(
                [executable, *command[1:], str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            while process.poll() is None:
                if cancel_event is not None and cancel_event.wait(0.05):
                    process.terminate()
                    process.wait(timeout=2)
                    raise VoiceOutputCancelled("playback interrompido")
            if process.returncode != 0:
                raise VoiceOutputError(
                    f"playback via {command[0]} terminou com código {process.returncode}"
                )
            return
        except VoiceOutputCancelled:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise VoiceOutputError(f"playback via {command[0]} falhou: {exc}") from exc
    raise VoiceOutputError(
        "nenhum player de áudio disponível; instale afplay, aplay, paplay ou ffplay"
    )


def synthesize_local_speech(
    text: str,
    output_file: str | Path,
    cancel_event: threading.Event | None = None,
    *,
    voice_name: str | None = None,
    rate: int | None = None,
) -> Path:
    """Gera voz local no Windows usando o Speech API nativo (SAPI)."""
    if os.name != "nt":
        raise VoiceOutputError("TTS local SAPI está disponível apenas no Windows")

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        raise VoiceOutputError("PowerShell não encontrado para o TTS local")

    destination = Path(output_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "if ($env:BAUER_TTS_LOCAL_VOICE) { "
        "  $synth.SelectVoice($env:BAUER_TTS_LOCAL_VOICE) "
        "}; "
        "$synth.Rate = [int]$env:BAUER_TTS_RATE; "
        "$synth.SetOutputToWaveFile($env:BAUER_TTS_OUTPUT); "
        "$synth.Speak($env:BAUER_TTS_TEXT); "
        "$synth.Dispose()"
    )
    environment = os.environ.copy()
    environment["BAUER_TTS_OUTPUT"] = str(destination)
    environment["BAUER_TTS_TEXT"] = str(text).strip()[:4096]
    environment["BAUER_TTS_LOCAL_VOICE"] = str(voice_name or "")
    environment["BAUER_TTS_RATE"] = str(configured_tts_rate() if rate is None else rate)
    encoded = script.encode("utf-16-le")
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        base64.b64encode(encoded).decode("ascii"),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            creationflags=creation_flags,
        )
        while process.poll() is None:
            if cancel_event is not None and cancel_event.wait(0.05):
                process.terminate()
                process.wait(timeout=2)
                raise VoiceOutputCancelled("síntese local interrompida")
        stdout, stderr = process.communicate(timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceOutputError(f"TTS local falhou: {exc}") from exc
    if process.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = (stderr or stdout or "sem detalhes").strip()[:300]
        raise VoiceOutputError(f"TTS local falhou: {detail}")
    return destination


def speak_response(
    text: str,
    client: Any,
    *,
    model: str = DEFAULT_TTS_MODEL,
    voice: str | None = None,
    cancel_event: threading.Event | None = None,
    metrics: VoiceTurnMetrics | None = None,
    barge_in: BargeInController | None = None,
) -> None:
    """Gera e reproduz uma resposta, removendo o arquivo temporário depois."""
    text = strip_emoji_for_speech(text)
    if not text:
        return
    fd, raw_path = tempfile.mkstemp(prefix="bauer-voice-", suffix=".wav")
    os.close(fd)
    path = Path(raw_path)
    effective_cancel_event = cancel_event or threading.Event()
    voice = configured_tts_voice(voice or DEFAULT_TTS_VOICE)
    controller = barge_in
    owns_controller = False
    playback_start: Callable[[], None] | None = None
    try:
        if effective_cancel_event.is_set():
            raise VoiceOutputCancelled("saída de voz interrompida")
        if metrics is not None:
            metrics.mark("tts_synthesis_start")
        provider = _tts_provider_pref()
        if provider not in {"auto", "local", "openai", "kokoro"}:
            raise VoiceOutputError(
                "BAUER_TTS_PROVIDER deve ser auto, local, openai ou kokoro"
            )
        if provider == "kokoro":
            from .voice_kokoro import synthesize_kokoro_speech

            synthesize_kokoro_speech(text, path)
        elif provider == "local":
            # ``local`` significa XTTS-v2. Isso inclui a referência WAV
            # configurada e mantém o mesmo caminho no streaming por frases e
            # no comando ``bauer voice speak``; SAPI fica reservado ao modo
            # ``auto`` quando nenhum TTS neural foi escolhido.
            result = synthesize_local_tts(text, output_path=path)
            if not result.get("success"):
                raise VoiceOutputError(str(result.get("error") or "TTS local falhou"))
        elif provider == "auto":
            try:
                synthesize_local_speech(
                    text,
                    path,
                    cancel_event=effective_cancel_event,
                    voice_name=os.environ.get("BAUER_TTS_LOCAL_VOICE", "").strip() or None,
                    rate=configured_tts_rate(),
                )
            except VoiceOutputCancelled:
                raise
            except VoiceOutputError:
                if provider == "local":
                    raise
                synthesize_speech(
                    text,
                    client,
                    path,
                    model=model,
                    voice=voice,
                    cancel_event=effective_cancel_event,
                )
        else:
            synthesize_speech(text, client, path, model=model, voice=voice, cancel_event=effective_cancel_event)
        if metrics is not None:
            metrics.mark("tts_synthesis_end")
        if controller is None:
            controller = BargeInController.from_environment(effective_cancel_event)
            owns_controller = controller is not None
        if controller is not None:
            playback_start = controller.prepare_playback(path)
        if metrics is not None:
            metrics.mark("tts_playback_start")
        play_audio(
            path,
            cancel_event=effective_cancel_event,
            playback_start=playback_start,
        )
        if metrics is not None:
            metrics.mark("tts_playback_end")
    except VoiceOutputCancelled:
        raise
    finally:
        if controller is not None:
            controller.end_playback()
            if owns_controller:
                controller.stop()
        try:
            path.unlink()
        except OSError:
            logger.debug("não foi possível remover áudio temporário: %s", path)
