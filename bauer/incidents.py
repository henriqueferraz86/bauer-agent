"""Telemetria de incidentes — captura estado quando algo falha em runtime.

Princípio: cada falha visível ao usuário (resposta vazia, loop detectado,
provider fora) vira um arquivo JSON em logs/incidents/ com o contexto da
falha. Incidentes são a matéria-prima para testes de regressão: o que
aconteceu em produção deve poder ser reproduzido em teste.

Uso::

    from bauer.incidents import record_incident

    record_incident(
        "empty_response",
        model=model_name,
        provider=provider,
        messages_count=len(payload),
        approx_tokens=approx_tokens,
    )

Design:
- Nunca levanta exceção (telemetria não pode quebrar o fluxo principal)
- Nunca grava conteúdo de mensagens (privacidade) — só métricas e metadados
- Arquivos pequenos, nome ordenável: YYYYMMDD_HHMMSS_<kind>.json
- Retenção: máximo INCIDENTS_MAX arquivos; mais antigos são removidos
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.incidents")

INCIDENTS_DIR = Path("logs") / "incidents"
INCIDENTS_MAX = 200  # retenção: acima disso, remove os mais antigos


def record_incident(kind: str, incidents_dir: Path | None = None, **details: Any) -> Path | None:
    """Grava um incidente em logs/incidents/. Retorna o path ou None em falha.

    `kind`: identificador curto (empty_response, tool_loop, provider_down...).
    `details`: metadados serializáveis — NUNCA inclua conteúdo de mensagens.
    """
    try:
        base = incidents_dir or INCIDENTS_DIR
        base.mkdir(parents=True, exist_ok=True)

        ts = time.localtime()
        stamp = time.strftime("%Y%m%d_%H%M%S", ts)
        # Sufixo anti-colisão para incidentes no mesmo segundo
        suffix = f"{int(time.time() * 1000) % 1000:03d}"
        path = base / f"{stamp}_{suffix}_{kind}.json"

        payload = {
            "kind": kind,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", ts),
            "details": _sanitize(details),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.warning("[incident] %s gravado em %s", kind, path)
        _enforce_retention(base)
        return path
    except Exception as exc:  # noqa: BLE001 — telemetria nunca propaga
        logger.debug("record_incident falhou (ignorado): %s", exc)
        return None


def list_incidents(incidents_dir: Path | None = None, kind: str | None = None) -> list[dict]:
    """Lê incidentes gravados (mais recentes primeiro). Para CLI/diagnóstico."""
    base = incidents_dir or INCIDENTS_DIR
    if not base.exists():
        return []
    out: list[dict] = []
    for f in sorted(base.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if kind and data.get("kind") != kind:
                continue
            data["_file"] = str(f)
            out.append(data)
        except Exception:
            continue
    return out


def _sanitize(details: dict[str, Any]) -> dict[str, Any]:
    """Remove valores não-serializáveis e trunca strings longas.

    Strings longas quase sempre são conteúdo (mensagem/resultado de tool) —
    truncar protege privacidade e mantém os arquivos pequenos.
    """
    clean: dict[str, Any] = {}
    for k, v in details.items():
        if isinstance(v, str) and len(v) > 500:
            clean[k] = v[:500] + f"... [truncado: {len(v)} chars]"
        elif isinstance(v, (str, int, float, bool, type(None))):
            clean[k] = v
        elif isinstance(v, (list, tuple)):
            clean[k] = [str(x)[:200] for x in list(v)[:20]]
        elif isinstance(v, dict):
            clean[k] = {str(kk): str(vv)[:200] for kk, vv in list(v.items())[:20]}
        else:
            clean[k] = str(v)[:200]
    return clean


def _enforce_retention(base: Path) -> None:
    files = sorted(base.glob("*.json"))
    excess = len(files) - INCIDENTS_MAX
    for f in files[:max(0, excess)]:
        try:
            f.unlink()
        except OSError:
            pass
