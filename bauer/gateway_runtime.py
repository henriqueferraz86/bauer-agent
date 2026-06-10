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

        for bridge in self.bridges:
            t = threading.Thread(
                target=self._run_bridge, args=(bridge,),
                name=f"bridge-{bridge.name}", daemon=True,
            )
            t.start()
            self._threads.append(t)

        pump = threading.Thread(target=self._outbox_pump, name="outbox-pump", daemon=True)
        pump.start()
        self._threads.append(pump)

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
        except KeyboardInterrupt:
            logger.info("Interrompido — desligando…")
        finally:
            self.stop()

    def _run_bridge(self, bridge: BaseBridge) -> None:
        """Roda um bridge com restart automático em crash (backoff)."""
        backoff = 5.0
        while not self._stop_event.is_set():
            try:
                bridge.start()
                break  # saída limpa (stop() chamado)
            except Exception as exc:  # noqa: BLE001
                bridge.last_error = str(exc)
                if self._stop_event.is_set():
                    break
                logger.error(
                    "Bridge %s caiu: %s — restart em %.0fs",
                    bridge.name, exc, backoff,
                )
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
        for bridge in self.bridges:
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


def run_gateway(config_path: str | Path = "config.yaml") -> None:
    """Entry point: python -m bauer.gateway_runtime."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    runtime = BauerGatewayRuntime.from_config(config_path)
    runtime.start(block=True)


if __name__ == "__main__":
    run_gateway()
