"""Commands for runtime events."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import typer

from ..core.events import EventBus
from ._common import console

events_app = typer.Typer(help="Inspeciona eventos auditaveis do runtime.")


@events_app.command("tail")
def events_tail(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    limit: int = typer.Option(50, "--limit", "-n", min=1),
    follow: bool = typer.Option(False, "--follow", "-f"),
    interval: float = typer.Option(1.0, "--interval", min=0.1),
):
    bus = EventBus(root=state_dir)
    seen: set[str] = set()

    def _print_new() -> None:
        for event in bus.list_events(limit=limit if not seen else None):
            if event.id in seen:
                continue
            seen.add(event.id)
            console.print(json.dumps(asdict(event), ensure_ascii=False))

    _print_new()
    while follow:
        time.sleep(interval)
        _print_new()
