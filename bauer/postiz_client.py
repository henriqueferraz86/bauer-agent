"""Cliente HTTP para a API pública do Postiz — agendamento/publicação em
redes sociais (X, LinkedIn, Instagram, TikTok, YouTube, Facebook, Reddit,
Pinterest, Threads, Bluesky, Mastodon…).

Postiz pode ser self-hosted (docker-compose, api_url local) ou a versão
hospedada (``https://api.postiz.com``, default). Auth via header
``Authorization`` com a API key crua (sem prefixo "Bearer").

Docs: https://docs.postiz.com/public-api

Uso::

    from bauer.postiz_client import PostizClient
    client = PostizClient(api_key="...")
    client.list_integrations()
    client.create_post("Olá mundo!", ["instagram-123"])
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "https://api.postiz.com"
_TIMEOUT_S = 30.0


class PostizError(RuntimeError):
    """Erro da API do Postiz (HTTP não-2xx, ou config ausente)."""


class PostizClient:
    """Chamadas REST à API pública do Postiz (``/public/v1/...``)."""

    def __init__(self, api_key: str, api_url: str = DEFAULT_API_URL) -> None:
        if not api_key.strip():
            raise PostizError("Postiz API key ausente — configure POSTIZ_API_KEY.")
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/")
        self._http = httpx.Client(
            base_url=self.api_url,
            timeout=_TIMEOUT_S,
            headers={"Authorization": api_key},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise PostizError(f"Postiz HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    def list_integrations(self, group: str | None = None) -> list[dict]:
        """Contas/redes sociais conectadas na instância Postiz."""
        params = {"group": group} if group else None
        return self._request("GET", "/public/v1/integrations", params=params)

    def list_groups(self) -> list[dict]:
        """Grupos (clientes) configurados — usado pra filtrar integrations."""
        return self._request("GET", "/public/v1/groups")

    def upload(self, file_path: str | Path) -> dict:
        """Sobe um arquivo local pro Postiz. Retorna dict com ``path`` (URL
        confiável exigida por TikTok/Instagram/YouTube antes de postar)."""
        p = Path(file_path)
        if not p.is_file():
            raise PostizError(f"Arquivo não encontrado: {p}")
        with p.open("rb") as fh:
            resp = self._http.post("/public/v1/upload", files={"file": (p.name, fh)})
        if resp.status_code >= 400:
            raise PostizError(f"Postiz upload HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def create_post(
        self,
        content: str,
        integration_ids: list[str],
        *,
        media_urls: list[str] | None = None,
        schedule_at: str | None = None,
        post_type: str = "schedule",
        settings: dict | None = None,
    ) -> Any:
        """Cria um post (agendado ou rascunho) numa ou mais integrações.

        ``schedule_at``: ISO 8601; se omitido, usa agora (UTC) — a API do
        Postiz exige ``date`` sempre, mesmo pra publicação imediata.
        ``media_urls``: URLs já enviadas via ``upload()`` (não aceita path
        local direto).
        """
        date = schedule_at or _dt.datetime.now(_dt.timezone.utc).isoformat()
        images = [{"id": str(i), "path": url} for i, url in enumerate(media_urls or [])]
        body = {
            "type": post_type,
            "creationMethod": "bauer-agent",
            "date": date,
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": iid},
                    "value": [{"content": content, "image": images, "delay": 0}],
                    "settings": settings,
                }
                for iid in integration_ids
            ],
        }
        return self._request("POST", "/public/v1/posts", json=body)

    def delete_post(self, post_id: str) -> Any:
        return self._request("DELETE", f"/public/v1/posts/{post_id}")

    def list_posts(
        self, start_date: str, end_date: str, customer: str | None = None
    ) -> list[dict]:
        params = {"startDate": start_date, "endDate": end_date}
        if customer:
            params["customer"] = customer
        return self._request("GET", "/public/v1/posts", params=params)

    def close(self) -> None:
        self._http.close()
