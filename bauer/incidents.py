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

Kinds conhecidos: empty_response, tool_loop, provider_down,
autonomous_loop_stopped (modo `/loop` do bauer agent — encerrado por
conclusão, orçamento esgotado, guardrail, erro de provider ou Ctrl+C;
detalhes em bauer/agent.py::_run_loop_mode).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.incidents")

#: Local ANTIGO: relativo ao CWD. Mantido só para leitura — o `list_incidents`
#: ainda varre aqui para não perder telemetria já gravada em pastas de projeto.
INCIDENTS_DIR = Path("logs") / "incidents"
INCIDENTS_MAX = 200  # retenção: acima disso, remove os mais antigos


def default_incidents_dir() -> Path:
    """Diretório canônico de incidentes: `$BAUER_HOME/logs/incidents`.

    Era `./logs/incidents`, relativo ao CWD. Como o `bauer run`/`bauer agent`
    roda de dentro do projeto do usuário, cada pasta de trabalho ganhava um
    `logs/incidents/` próprio — a telemetria ficava espalhada (e portanto
    inagregável), e em CWD read-only sumia em silêncio, já que
    `record_incident` engole toda exceção. Mesma classe de bug do log do
    doctor (PR #94), que na época foi corrigido só no ponto reportado.

    Resolvido em tempo de CHAMADA, não no import, para respeitar $BAUER_HOME
    definido depois (testes, CI).
    """
    from .paths import get_bauer_home

    return get_bauer_home() / "logs" / "incidents"


def record_incident(kind: str, incidents_dir: Path | None = None, **details: Any) -> Path | None:
    """Grava um incidente em logs/incidents/. Retorna o path ou None em falha.

    `kind`: identificador curto (empty_response, tool_loop, provider_down...).
    `details`: metadados serializáveis — NUNCA inclua conteúdo de mensagens.
    """
    try:
        base = incidents_dir or default_incidents_dir()
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
        # info, não warning: incidente é telemetria de rotina — em WARNING ele
        # estourava no console do chat junto da resposta, virando ruído.
        logger.info("[incident] %s gravado em %s", kind, path)
        _enforce_retention(base)
        return path
    except Exception as exc:  # noqa: BLE001 — telemetria nunca propaga
        logger.debug("record_incident falhou (ignorado): %s", exc)
        return None


def list_incidents(incidents_dir: Path | None = None, kind: str | None = None) -> list[dict]:
    """Lê incidentes gravados (mais recentes primeiro). Para CLI/diagnóstico.

    Sem `incidents_dir` explícito, varre o diretório canônico E o antigo
    `./logs/incidents` do CWD — assim a telemetria escrita antes da mudança
    de local continua aparecendo em vez de sumir do relatório.
    """
    if incidents_dir is not None:
        bases = [incidents_dir]
    else:
        bases = [default_incidents_dir(), INCIDENTS_DIR]

    out: list[dict] = []
    seen: set[str] = set()
    for base in bases:
        if not base.exists() or str(base.resolve()) in seen:
            continue
        seen.add(str(base.resolve()))
        for f in sorted(base.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if kind and data.get("kind") != kind:
                    continue
                data["_file"] = str(f)
                out.append(data)
            except Exception:
                continue
    # Mais recentes primeiro, agora ordenando ENTRE os diretórios.
    out.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
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
