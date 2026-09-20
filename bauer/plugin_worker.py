"""Minimal child process for the managed plugin JSONL protocol."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from .plugin_hooks import hooks
from .plugin_process import PROTOCOL_VERSION


def _write(response: dict[str, Any]) -> None:
    stdout = sys.__stdout__
    if stdout is None:
        raise RuntimeError("managed plugin worker has no stdout")
    stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    stdout.flush()


def _load_entrypoint(path: Path, plugin_id: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(f"bauer_managed_plugin_{plugin_id}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not create plugin import spec")
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)


def run(plugin_id: str, entry_point: Path) -> int:
    try:
        _load_entrypoint(entry_point, plugin_id)
    except Exception as exc:  # noqa: BLE001 - child reports a bounded error
        _write({"protocol": PROTOCOL_VERSION, "request_id": "", "ok": False, "error": str(exc)[:300]})
        return 2

    for raw_line in sys.stdin.buffer:
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = str(request.get("request_id", ""))
            if request.get("protocol") != PROTOCOL_VERSION or not request_id:
                raise ValueError("invalid protocol header")
            kind = request.get("kind")
            payload = request.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            if kind == "hello":
                result: dict[str, Any] = {"plugin_id": plugin_id, "protocol": PROTOCOL_VERSION}
            elif kind == "health":
                result = {"plugin_id": plugin_id, "state": "ready"}
            elif kind == "event":
                event = str(payload.get("event", "")).strip()
                event_payload = payload.get("payload", {})
                if not event or not isinstance(event_payload, dict):
                    raise ValueError("event and object payload are required")
                with contextlib.redirect_stdout(sys.stderr):
                    hooks.emit(event, **event_payload)
                result = {"event": event, "dispatched": True}
            elif kind == "shutdown":
                _write({"protocol": PROTOCOL_VERSION, "request_id": request_id, "ok": True, "result": {}})
                return 0
            else:
                raise ValueError(f"unknown request kind: {kind}")
            _write({"protocol": PROTOCOL_VERSION, "request_id": request_id, "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            _write({"protocol": PROTOCOL_VERSION, "request_id": locals().get("request_id", ""), "ok": False, "error": str(exc)[:300]})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bauer managed plugin worker")
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--entry-point", required=True, type=Path)
    args = parser.parse_args()
    return run(args.plugin_id, args.entry_point)


if __name__ == "__main__":
    raise SystemExit(main())
