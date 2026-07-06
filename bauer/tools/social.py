"""Social tools (Postiz): social_list_channels e social_post.

Mixin herdado por ToolRouter. Publica/agenda em redes sociais reais
(Instagram, X, LinkedIn, TikTok, YouTube, Facebook…) via Postiz —
self-hosted ou hospedado. Credenciais chegam prontas do config.yaml/.env
via ``_build_router`` (self._postiz_api_key/self._postiz_api_url).
"""

from __future__ import annotations

from .base import ToolError


class SocialToolsMixin:
    """Publicação em redes sociais via Postiz."""

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

    def _social_post(self, args: dict) -> str:
        """Publica ou agenda um post em uma ou mais redes sociais via Postiz."""
        content = str(args.get("content", "")).strip()
        channels = args.get("channels")
        media_paths = args.get("media_paths") or []
        schedule_at = str(args.get("schedule_at", "")).strip() or None
        post_type = str(args.get("post_type", "schedule")).strip() or "schedule"

        if not content:
            raise ToolError("social_post requer 'content'.")
        if not channels or not isinstance(channels, list):
            raise ToolError(
                "social_post requer 'channels' (lista de integration ids). "
                "Use social_list_channels para ver os IDs disponíveis."
            )
        if post_type not in ("schedule", "draft"):
            raise ToolError("post_type deve ser 'schedule' ou 'draft'.")

        client = self._postiz_client()
        try:
            media_urls: list[str] = []
            for path in media_paths:
                uploaded = client.upload(path)
                url = uploaded.get("path") or uploaded.get("url")
                if not url:
                    raise ToolError(f"Upload de '{path}' não retornou URL utilizável.")
                media_urls.append(url)

            client.create_post(
                content,
                [str(c) for c in channels],
                media_urls=media_urls or None,
                schedule_at=schedule_at,
                post_type=post_type,
            )
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Falha ao publicar via Postiz: {exc}") from exc

        state = "agendado" if post_type == "schedule" else "salvo como rascunho"
        return f"Post {state} em {len(channels)} canal(is) via Postiz."
