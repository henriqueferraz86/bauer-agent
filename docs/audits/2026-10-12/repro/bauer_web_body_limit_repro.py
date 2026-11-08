"""WEB-10: receive ASGI contado, limite pequeno, corpo sem Content-Length.

Antes da sprint 63 o app consome todos os chunks e só então rejeita;
depois, o middleware corta no limite (poucos chunks) com 413.
"""
import asyncio, json, os, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sandbox = Path(tempfile.mkdtemp(prefix="bauer-web10-"))
os.environ.update(BAUER_HOME=str(sandbox / "home"), BAUER_CONFIG=str(sandbox / "absent.yaml"),
                  BAUER_AGENTS_FILE=str(sandbox / "absent-agents.yaml"))

import bauer.transcription as tr
from bauer.server import create_app
from bauer.tool_router import ToolRouter

tr.MAX_AUDIO_BYTES = 4096  # small threshold for the probe
try:
    import bauer.server_body_limit as bl  # exists only after the fix
    bl.MULTIPART_OVERHEAD_BYTES = 1024
    bl.DEFAULT_MAX_BODY_BYTES = 8192
except ImportError:
    pass
client = MagicMock()
with patch("bauer.agent.run_one_turn", return_value=("ok", [])):
    app = create_app(model_name="m", applied_context=4096, router=ToolRouter(workspace=sandbox),
                     client=client, system_prompt="s", sessions_dir=sandbox / "sessions",
                     api_key="", rate_limit_requests=0)


async def call(path, chunks, content_type):
    consumed = [0]
    it = iter(chunks)

    async def receive():
        try:
            body = next(it)
        except StopIteration:
            return {"type": "http.disconnect"}
        consumed[0] += 1
        return {"type": "http.request", "body": body, "more_body": consumed[0] < len(chunks)}

    status = []

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": [(b"content-type", content_type.encode()), (b"host", b"t")],
             "client": ("127.0.0.1", 1), "server": ("t", 80)}
    await app(scope, receive, send)
    return consumed[0], status


boundary = "XBOUNDARY"
head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.webm\"\r\n"
        "Content-Type: audio/webm\r\n\r\n").encode()
chunks = [head] + [b"x" * 1024] * 200 + [f"\r\n--{boundary}--\r\n".encode()]
n, st = asyncio.run(call("/transcribe", chunks, f"multipart/form-data; boundary={boundary}"))
print(json.dumps({"case": "multipart_chunked_transcribe", "chunks_total": len(chunks),
                  "chunks_consumed": n, "status": st}))

chunks = [b'{"message": "'] + [b"y" * 1024] * 300 + [b'"}']
n, st = asyncio.run(call("/chat", chunks, "application/json"))
print(json.dumps({"case": "json_chunked_chat", "chunks_total": len(chunks),
                  "chunks_consumed": n, "status": st}))
