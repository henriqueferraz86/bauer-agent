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
