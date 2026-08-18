"""Messaging tools: canais de chat (Bauer Gateway) e redes sociais (Postiz).

Mixin herdado por ToolRouter. Fusão de channel.py + social.py (2026-08-18) —
os dois eram mixins pequenos pro mesmo domínio geral (mandar mensagem/post
pra fora), um para chat (Telegram/Discord via ..gateway_outbox/
..gateway_channels), outro para redes sociais (Instagram/X/LinkedIn/etc. via
Postiz, credenciais em self._postiz_api_key/self._postiz_api_url, injetadas
por ``_build_router``).
"""

from __future__ import annotations

from .base import ToolError


class MessagingToolsMixin:
    """Envio de mensagens a canais de chat (Telegram/Discord) e posts em redes sociais."""

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

    def _send_message(self, args: dict) -> str:
        """Envia mensagem direto pelo bridge vivo do gateway (ou outbox).

        Diferença para channel_send: aqui o destino é um chat_id REAL de um
        canal inbound (telegram/discord). Com o gateway no mesmo processo a
        entrega é imediata, incluindo mídia. Sem gateway vivo, enfileira no
        outbox durável para o próximo `bauer gateway start`.
        """
        channel = str(args.get("channel", "")).strip().lower()
        chat_id = str(args.get("chat_id", "")).strip()
        text = str(args.get("text", "")).strip()
        media_path = str(args.get("media_path", "")).strip()
        if not channel:
            raise ToolError("send_message requer 'channel' (telegram/discord).")
        if not chat_id:
            raise ToolError("send_message requer 'chat_id' (id do chat destino).")
        if not text and not media_path:
            raise ToolError("send_message requer 'text' e/ou 'media_path'.")

        from .. import live_bridges
        bridge = live_bridges.get(channel)
        if bridge is not None:
            sent: list[str] = []
            if text:
                bridge.send_text(chat_id, text)
                sent.append("texto")
            if media_path:
                send_media = getattr(bridge, "send_media", None)
                if send_media is None:
                    raise ToolError(f"Canal '{channel}' não suporta envio de mídia.")
                if not send_media(chat_id, media_path):
                    raise ToolError(f"Falha enviando mídia '{media_path}' via {channel}.")
                sent.append("mídia")
            return f"Mensagem ({' + '.join(sent)}) entregue em {channel}:{chat_id}."

        # Gateway não está neste processo — outbox durável
        from ..gateway_outbox import GatewayOutbox
        payload: dict = {"text": text, "source": "send_message"}
        if media_path:
            payload["media_path"] = media_path
        message = GatewayOutbox(self.workspace).enqueue(
            channel=channel, target=chat_id, payload=payload, metadata={},
        )
        return (
            f"Gateway não está rodando neste processo — mensagem enfileirada "
            f"(id={message.message_id}); será entregue quando `bauer gateway start` subir."
        )

    def _postiz_client(self):
        api_key = getattr(self, "_postiz_api_key", "") or ""
        if not api_key.strip():
            raise ToolError(
                "POSTIZ_API_KEY ausente. Configure no .env ou em postiz.api_key "
                "no config.yaml. Veja https://docs.postiz.com/public-api."
            )
        api_url = getattr(self, "_postiz_api_url", "") or "https://api.postiz.com"
        from ..postiz_client import PostizClient, PostizError

        try:
            return PostizClient(api_key=api_key, api_url=api_url)
        except PostizError as exc:
            raise ToolError(str(exc)) from exc

    def _social_list_channels(self, args: dict) -> str:
        """Lista as contas/redes sociais conectadas na instância Postiz."""
        client = self._postiz_client()
        try:
            integrations = client.list_integrations()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Falha ao listar integrações do Postiz: {exc}") from exc

        if not integrations:
            return "Nenhuma conta conectada no Postiz. Conecte em /settings da instância."
        lines = ["Contas conectadas no Postiz:"]
        for it in integrations:
            name = it.get("name") or it.get("profile") or "?"
            provider = it.get("identifier") or "?"
            state = " (desabilitado)" if it.get("disabled") else ""
            lines.append(f"- {it.get('id')} — {provider}: {name}{state}")
        return "\n".join(lines)

    # Default de settings por plataforma — algumas exigem campos obrigatórios
    # próprios (ex.: Instagram exige settings.post_type = "post"|"story", senão
    # a API rejeita com 400 "should not be null or undefined"). Aplicado só
    # quando o caller não passou 'settings' explicitamente.
    _PLATFORM_DEFAULT_SETTINGS = {
        "instagram": {"post_type": "post"},
        "instagram-standalone": {"post_type": "post"},
    }
    # post_type da tool "story" mapeia para schedule + settings.post_type=story
    # (feito no create_post automaticamente)

    def _default_settings_for(self, channel_ids: list[str]) -> dict | None:
        """Resolve o identifier de cada canal e devolve o default da 1ª
        plataforma reconhecida (Postiz aplica os mesmos settings a todos os
        canais do post — não há por-canal na API pública)."""
        try:
            integrations = {i.get("id"): i.get("identifier") for i in self._postiz_client().list_integrations()}
        except Exception:  # noqa: BLE001 — best-effort, não bloqueia o post
            return None
        for cid in channel_ids:
            identifier = integrations.get(cid, "")
            for prefix, defaults in self._PLATFORM_DEFAULT_SETTINGS.items():
                if identifier.startswith(prefix):
                    return dict(defaults)
        return None

    def _social_post(self, args: dict) -> str:
        """Publica ou agenda um post em uma ou mais redes sociais via Postiz.

        Duas formas de anexar mídia:
        - media_urls: URL já pública (ex.: retorno do image_generate via
          provider xai/openrouter) — usada direto, sem reenvio.
        - media_paths: arquivo local — sobe pro storage do PRÓPRIO Postiz
          (client.upload). Em instância self-hosted sem storage público
          (Cloudflare R2/S3), isso devolve uma URL localhost que plataformas
          como Instagram REJEITAM ("Media fetch failed") — prefira
          media_urls quando o provider de geração já dá URL pública.
        """
        content = str(args.get("content", "")).strip()
        channels = args.get("channels")
        media_paths = args.get("media_paths") or []
        media_urls_arg = list(args.get("media_urls") or [])
        schedule_at = str(args.get("schedule_at", "")).strip() or None
        post_type = str(args.get("post_type", "schedule")).strip() or "schedule"
        settings = args.get("settings")

        if not content:
            raise ToolError("social_post requer 'content'.")
        if not channels or not isinstance(channels, list):
            raise ToolError(
                "social_post requer 'channels' (lista de integration ids). "
                "Use social_list_channels para ver os IDs disponíveis."
            )
        if post_type not in ("schedule", "draft", "story", "now"):
            raise ToolError("post_type deve ser 'schedule', 'draft', 'story' ou 'now'.")

        client = self._postiz_client()
        channel_ids = [str(c) for c in channels]
        if settings is None:
            settings = self._default_settings_for(channel_ids)

        try:
            media_urls: list[str] = list(media_urls_arg)
            for path in media_paths:
                uploaded = client.upload(path)
                url = uploaded.get("path") or uploaded.get("url")
                if not url:
                    raise ToolError(f"Upload de '{path}' não retornou URL utilizável.")
                if url.startswith("http://localhost") or url.startswith("http://127.0.0.1"):
                    raise ToolError(
                        f"Upload de '{path}' voltou uma URL local ({url}) — "
                        "plataformas como Instagram não conseguem baixar mídia "
                        "de localhost. Gere a imagem com um provider que retorna "
                        "URL pública (ex.: image_generate provider=xai) e passe "
                        "o resultado via 'media_urls' em vez de 'media_paths', "
                        "ou configure storage público (Cloudflare R2/S3) no Postiz."
                    )
                media_urls.append(url)

            client.create_post(
                content,
                channel_ids,
                media_urls=media_urls or None,
                schedule_at=schedule_at,
                post_type=post_type,
                settings=settings,
            )
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Falha ao publicar via Postiz: {exc}") from exc

        state = "agendado" if post_type == "schedule" else "salvo como rascunho"
        return f"Post {state} em {len(channels)} canal(is) via Postiz."
