"""Bauer Gateway Runtime — orquestra os canais de chat + entrega do outbox.

``bauer gateway start`` sobe este runtime: cada canal habilitado no
config.yaml (telegram, discord, …) roda numa thread própria compartilhando
UM ``AgentBackend`` (mesmo client/router/sessões); em paralelo, um pump
drena o ``GatewayOutbox`` — as mensagens enfileiradas pela tool
``channel_send`` e por escalations são efetivamente entregues aqui.

Uso programático::

    runtime = BauerGatewayRuntime.from_config("config.yaml")
    runtime.start()          # bloqueia até Ctrl+C / stop()

Shutdown: SIGINT/KeyboardInterrupt → ``stop()`` em cadeia em todos os
bridges + pump, com join de threads (timeout curto — bridges em long-poll
saem no próximo ciclo).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .channel_base import AgentBackend, BaseBridge

logger = logging.getLogger("bauer.gateway")


class BauerGatewayRuntime:
    """Supervisor dos canais inbound + pump do outbox outbound."""

    def __init__(
        self,
        backend: AgentBackend,
        bridges: list[BaseBridge],
        workspace: str | Path = "workspace",
        outbox_drain_interval_s: int = 15,
    ) -> None:
        self.backend = backend
        self.bridges = bridges
        self.workspace = Path(workspace)
        self.outbox_drain_interval_s = max(1, int(outbox_drain_interval_s))
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._pump_stats = {"delivered": 0, "failed": 0, "last_run": ""}

    # ── Construção a partir do config ──────────────────────────────────────

    @classmethod
    def from_config(cls, config_path: str | Path = "config.yaml") -> "BauerGatewayRuntime":
        """Monta o runtime com os canais habilitados no config.yaml."""
        from .config_loader import load_config

        cfg = load_config(config_path)
        backend = AgentBackend(config_path=config_path)
        bridges: list[BaseBridge] = []

        if cfg.telegram.enabled:
            from .telegram_bridge import build_bridge_from_config as build_tg
            bridges.append(build_tg(cfg, backend))
        if cfg.discord.enabled:
            from .discord_bridge import build_bridge_from_config as build_dc
            bridges.append(build_dc(cfg, backend))

        return cls(
            backend=backend,
            bridges=bridges,
            workspace=cfg.agent.workspace,
            outbox_drain_interval_s=cfg.gateway.outbox_drain_interval_s,
        )

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def start(self, block: bool = True) -> None:
        """Sobe bridges + pump. ``block=True`` segura até Ctrl+C/stop()."""
        if not self.bridges:
            logger.warning(
                "Nenhum canal habilitado no config.yaml "
                "(telegram.enabled / discord.enabled). Rode `bauer gateway init`."
            )

        from . import live_bridges
        for bridge in self.bridges:
            live_bridges.register(bridge.name, bridge)  # tool send_message
            t = threading.Thread(
                target=self._run_bridge, args=(bridge,),
                name=f"bridge-{bridge.name}", daemon=True,
            )
            t.start()
            self._threads.append(t)

        pump = threading.Thread(target=self._outbox_pump, name="outbox-pump", daemon=True)
        pump.start()
        self._threads.append(pump)

        # Aquece o modelo Whisper local (se STT_PROVIDER resolver p/ 'local') em
        # background — p/ a 1ª voice note não pagar os ~86s de carga no meio do
        # turno (o que estourava o typing e travava a resposta). Auto-gated.
        try:
            from .transcription import preload_local_model
            preload_local_model()
        except Exception:  # noqa: BLE001 — preload nunca deve impedir o boot
            logger.debug("preload do Whisper pulado", exc_info=True)

        logger.info(
            "Bauer Gateway no ar — canais: %s | outbox a cada %ds",
            ", ".join(b.name for b in self.bridges) or "nenhum",
            self.outbox_drain_interval_s,
        )
        if not block:
            return
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        finally:
            self.stop()

    def _run_bridge(self, bridge: BaseBridge) -> None:
        """Roda um bridge com restart automático em crash (backoff)."""
        backoff = 5.0
        while not self._stop_event.is_set():
            try:
                bridge.start()
            except Exception as exc:  # noqa: BLE001
                bridge.last_error = str(exc)
                if self._stop_event.is_set():
                    break
                logger.error(
                    "Bridge %s caiu: %s — restart em %.0fs",
                    bridge.name, exc, backoff,
                )
            else:
                # start() retornou sem exceção
                if self._stop_event.is_set():
                    break  # saída limpa — gateway parando
                # Bridge saiu inesperadamente sem o gateway ter pedido — reinicia
                logger.warning(
                    "Bridge %s saiu sem stop() do gateway — reiniciando em %.0fs",
                    bridge.name, backoff,
                )
            bridge.reset()  # limpa stop_event do bridge para o próximo start()
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2, 120.0)

    def _outbox_pump(self) -> None:
        """Drena o GatewayOutbox periodicamente (channel_send/escalations)."""
        from .gateway_outbox import GatewayOutbox

        outbox = GatewayOutbox(self.workspace)
        while not self._stop_event.is_set():
            try:
                result = outbox.deliver_once(limit=20)
                if result.delivered or result.failed:
                    self._pump_stats["delivered"] += len(result.delivered)
                    self._pump_stats["failed"] += len(result.failed)
                    logger.info(
                        "Outbox: %d entregues, %d falharam",
                        len(result.delivered), len(result.failed),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Outbox pump falhou: %s", exc)
            self._pump_stats["last_run"] = time.strftime("%H:%M:%S")
            self._stop_event.wait(self.outbox_drain_interval_s)

    def stop(self) -> None:
        self._stop_event.set()
        from . import live_bridges
        for bridge in self.bridges:
            live_bridges.unregister(bridge.name)
            try:
                bridge.stop()
            except Exception:  # noqa: BLE001
                pass
        for t in self._threads:
            t.join(timeout=5.0)
        logger.info("Bauer Gateway parado.")

    # ── Observabilidade ────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        from .gateway_outbox import GatewayOutbox

        try:
            outbox_pending = len(GatewayOutbox(self.workspace).pending(limit=100))
        except Exception:  # noqa: BLE001
            outbox_pending = -1
        return {
            "running": not self._stop_event.is_set(),
            "bridges": [b.status() for b in self.bridges],
            "backend": {
                "ready": self.backend.is_ready,
                "msgs_processed": self.backend.msgs_processed,
                "errors": self.backend.errors,
            },
            "outbox": {**self._pump_stats, "pending": outbox_pending},
        }


def _setup_service_logging() -> None:
    """Console + arquivo rotativo logs/gateway.log.

    Como serviço (Task Scheduler/systemd) não há console visível — o arquivo
    é a fonte de `bauer gateway service logs` no Windows; no Linux o journald
    também captura o stream.
    """
    from logging.handlers import RotatingFileHandler

    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        fileh = RotatingFileHandler(
            log_dir / "gateway.log", maxBytes=5 * 1024 * 1024,
            backupCount=3, encoding="utf-8",
        )
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        logger.warning("Sem permissão para logs/gateway.log — só console")


def _write_pid_file(workspace: Path) -> Path | None:
    """PID file para `service status` saber se o processo está vivo."""
    import os

    pid_file = workspace / ".bauer_gateway" / "gateway.pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        return pid_file
    except OSError:
        return None


def run_gateway(config_path: str | Path = "config.yaml") -> None:
    """Entry point: python -m bauer.gateway_runtime (foreground e serviço).

    Loop de restart interno: qualquer exceção inesperada (config, rede, crash)
    aguarda restart_delay e tenta de novo. KeyboardInterrupt (Ctrl+C ou
    SIGINT) sai limpo. Isso garante que o Task Scheduler / systemd / launchd
    não precisam fazer o restart — o processo cuida de si mesmo.
    """
    _setup_service_logging()
    pid_file: Path | None = None
    restart_delay = 10  # segundos; dobra a cada falha, max 120

    try:
        while True:
            try:
                runtime = BauerGatewayRuntime.from_config(config_path)
                if pid_file is None:
                    pid_file = _write_pid_file(Path(runtime.workspace))
                runtime.start(block=True)
                # start() retornou sem KeyboardInterrupt = saída limpa via stop()
                logger.info("Gateway encerrado normalmente.")
                break
            except KeyboardInterrupt:
                logger.info("Interrompido pelo usuário (KeyboardInterrupt).")
                break
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Gateway caiu inesperadamente: %s — reiniciando em %ds",
                    exc, restart_delay,
                )
                time.sleep(restart_delay)
                restart_delay = min(restart_delay * 2, 120)
    finally:
        if pid_file is not None:
            try:
                pid_file.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    run_gateway()
