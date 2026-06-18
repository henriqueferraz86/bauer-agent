"""Shell WebSocket server — FastAPI app que expõe /ws/shell.

Uso standalone::

    python -m bauer.shell_server --port 7782

Ou incorporado em outra app FastAPI::

    from bauer.shell_server import app as shell_app
    main_app.mount("/shell", shell_app)
"""

from __future__ import annotations

import argparse
import sys


def create_app():
    """Cria e retorna a FastAPI app do shell server."""
    try:
        from fastapi import FastAPI, WebSocket
    except ImportError:
        raise ImportError("fastapi required: pip install 'bauer-agent[gateway]'")

    from .pty_bridge import PtyBridge

    app = FastAPI(title="Bauer Shell WebSocket", version="1.0")

    @app.websocket("/ws/shell")
    async def shell_ws(ws: WebSocket):
        await ws.accept()
        bridge = PtyBridge()
        await bridge.start_session(ws)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "bauer-shell"}

    return app


app = None  # lazily created


def get_app():
    global app
    if app is None:
        app = create_app()
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bauer Shell WebSocket server")
    parser.add_argument("--port", type=int, default=7782)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--shell", default=None, help="Shell command (default: platform default)")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print("uvicorn required: pip install 'bauer-agent[gateway]'", file=sys.stderr)
        sys.exit(1)

    print(f"Bauer Shell WebSocket → ws://{args.host}:{args.port}/ws/shell")
    uvicorn.run(get_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
