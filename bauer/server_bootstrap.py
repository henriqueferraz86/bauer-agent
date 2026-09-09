"""Dependências opcionais e aquecimento best-effort do processo HTTP."""

from __future__ import annotations

from pathlib import Path


def effective_workspace(router) -> Path | None:
    """Converte somente workspaces reais, evitando paths artificiais de mocks."""
    workspace = getattr(router, "workspace", None)
    return Path(workspace) if isinstance(workspace, (str, Path)) else None


def require_fastapi() -> None:
    """Falha cedo com instrução acionável quando a extra de servidor falta."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "FastAPI/uvicorn nao instalados.\n"
            "Instale com: pip install 'bauer-agent[server]'\n"
            "Ou: pip install fastapi uvicorn[standard]"
        ) from exc


def warmup_ollama_model(host: str, model: str) -> None:
    """Solicita o carregamento do modelo em background; falhas não afetam o serve."""
    import threading

    def _load() -> None:
        try:
            import httpx

            httpx.post(
                f"{host.rstrip('/')}/api/generate",
                json={"model": model, "prompt": "", "stream": False, "keep_alive": "30m",
                      "options": {"num_predict": 0}},
                timeout=120.0,
            )
        except Exception as exc:  # noqa: BLE001 -- aquecimento é estritamente auxiliar
            from .logging_config import log_suppressed
            log_suppressed("server.warmup_ollama", exc)

    threading.Thread(target=_load, daemon=True, name=f"warmup-{model}").start()
