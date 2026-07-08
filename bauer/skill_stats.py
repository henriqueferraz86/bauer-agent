"""Telemetria de uso de skills — Nível 1 (só observabilidade, NÃO age).

Registra, por skill, quantas vezes foi injetada num turno e a cara do
resultado daquele turno (bom/ruim/neutro, sinal grosseiro derivado do desfecho
do turno) + o feedback humano 👍/👎. Nenhuma ação automática: os números só
existem para você ver (`bauer skills stats`) e para, MAIS TARDE, embasar o
degrau 3 (refinar/rebaixar skill) com dado real.

AVISO honesto de atribuição: a skill estar presente no turno NÃO prova que ela
causou o desfecho — o turno depende do modelo, da tarefa e das tools. E skills
disparam em tarefas mais difíceis (viés). Portanto: NÃO tire conclusão de
poucos usos, e NÃO use "taxa de sucesso crua" para julgar uma skill. Isto aqui
é coleta; a interpretação segura (comparar com-vs-sem em tarefas parecidas)
fica para quando houver volume.

Store: ``$BAUER_HOME/skill_stats.json`` — ``{skill_name: {uses, good, bad,
neutral, thumbs_up, thumbs_down, last_used}}``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_VALID_OUTCOMES = ("good", "bad", "neutral")


def _stats_path() -> Path:
    from .paths import get_bauer_home
    return get_bauer_home() / "skill_stats.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _blank() -> dict[str, Any]:
    return {"uses": 0, "good": 0, "bad": 0, "neutral": 0,
            "thumbs_up": 0, "thumbs_down": 0, "last_used": 0.0}


def _save(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # telemetria nunca derruba o turno


def record_use(skill_name: str, outcome: str, *, path: "Path | None" = None) -> None:
    """Incrementa uso + bucket de desfecho da skill. Best-effort."""
    name = (skill_name or "").strip()
    if not name:
        return
    if outcome not in _VALID_OUTCOMES:
        outcome = "neutral"
    p = path or _stats_path()
    data = _load(p)
    rec = data.get(name) or _blank()
    rec["uses"] = int(rec.get("uses", 0)) + 1
    rec[outcome] = int(rec.get(outcome, 0)) + 1
    rec["last_used"] = time.time()
    data[name] = rec
    _save(p, data)


def record_feedback(skill_name: str, positive: bool, *, path: "Path | None" = None) -> None:
    """Registra o 👍/👎 humano na skill que estava ativa no turno avaliado."""
    name = (skill_name or "").strip()
    if not name:
        return
    p = path or _stats_path()
    data = _load(p)
    rec = data.get(name) or _blank()
    key = "thumbs_up" if positive else "thumbs_down"
    rec[key] = int(rec.get(key, 0)) + 1
    data[name] = rec
    _save(p, data)


def load_stats(*, path: "Path | None" = None) -> dict[str, Any]:
    """Todas as stats (para exibição). Dict vazio se não há coleta ainda."""
    return _load(path or _stats_path())
