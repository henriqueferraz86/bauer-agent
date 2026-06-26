"""Channel tools (Bauer Gateway): channel_send e channel_list.

Mixin herdado por ToolRouter. Entrega via ..gateway_outbox (duravel) e
descoberta de canais via ..gateway_channels.
"""

from __future__ import annotations

from .base import ToolError


class ChannelToolsMixin:
    """Envio de mensagens a canais de chat (Telegram/Discord) via outbox."""

    def _channel_send(self, args: dict) -> str:
        """Envia mensagem a um canal do gateway via outbox durável.

        A mensagem NÃO é entregue inline — entra no GatewayOutbox (SQLite)
        e o `bauer gateway start` (pump) entrega com retry. Isso torna o
        envio auditável e resiliente a quedas de rede no meio do turno.
        """
        channel_name = str(args.get("channel", "")).strip()
        text = str(args.get("text", "")).strip()
        if not channel_name:
            raise ToolError("channel_send requer 'channel'. Use channel_list para ver os nomes.")
        if not text:
            raise ToolError("channel_send requer 'text'.")

        from ..gateway_channels import GatewayChannelRegistry
        from ..gateway_outbox import GatewayOutbox

        registry = GatewayChannelRegistry(self.workspace)
        entry = registry.get(channel_name)
        if entry is None:
            known = ", ".join(c.name for c in registry.list_channels()) or "(nenhum)"
            raise ToolError(
                f"Canal '{channel_name}' não existe. Canais configurados: {known}. "
                "Registre com: bauer gateway-channel-add <nome> <plataforma> <target>"
            )
        if not entry.enabled:
            raise ToolError(f"Canal '{channel_name}' está desabilitado.")

        outbox = GatewayOutbox(self.workspace)
        message = outbox.enqueue(
            channel=entry.platform,
            target=entry.target,
            payload={"text": text, "source": "channel_send"},
            metadata=dict(entry.metadata),
        )
        return (
            f"Mensagem enfileirada para '{channel_name}' ({entry.platform}). "
            f"id={message.message_id} — entrega via `bauer gateway start`."
        )

    def _channel_list(self, args: dict) -> str:
        """Lista canais de notificação registrados no gateway."""
        from ..gateway_channels import GatewayChannelRegistry

        registry = GatewayChannelRegistry(self.workspace)
        channels = registry.list_channels(include_disabled=True)
        if not channels:
            return (
                "Nenhum canal configurado. Registre com: "
                "bauer gateway-channel-add <nome> <plataforma> <target>"
            )
        lines = ["Canais do Bauer Gateway:"]
        for c in channels:
            state = "on" if c.enabled else "off"
            lines.append(f"- {c.name} [{c.platform}] → {c.target} ({state})")
        return "\n".join(lines)
