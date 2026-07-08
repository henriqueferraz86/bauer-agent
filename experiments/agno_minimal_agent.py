"""Minimal Agno spike outside Bauer.

This experiment intentionally uses a deterministic local Agno Model so the
runtime surface can be tested without OpenAI/OpenRouter credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.base import Model
from agno.models.message import Message
from agno.models.response import ModelResponse


def add_numbers(a: int, b: int) -> str:
    """Return a small deterministic sum for tool-call validation."""

    return str(a + b)


class OfflineEchoModel(Model):
    """A tiny Agno model used only for local runtime validation.

    It echoes the last user message, exposes recent session history, and emits a
    provider-style tool call when the user asks for the add_numbers tool.
    """

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        messages = kwargs.get("messages") or []

        if messages and messages[-1].role == "tool":
            return ModelResponse(
                role="assistant",
                content=f"tool result received: {messages[-1].content}",
            )

        last_user = self._last_user_message(messages)
        if "tool" in last_user.lower() or "soma" in last_user.lower():
            return ModelResponse(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_add_numbers_1",
                        "type": "function",
                        "function": {
                            "name": "add_numbers",
                            "arguments": json.dumps({"a": 2, "b": 3}),
                        },
                    }
                ],
            )

        return ModelResponse(role="assistant", content=self._echo_with_history(messages))

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        response = self.invoke(*args, **kwargs)
        if response.tool_calls:
            yield response
            return

        content = str(response.content or "")
        for chunk in self._chunks(content):
            yield ModelResponse(role="assistant", content=chunk)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        for chunk in self.invoke_stream(*args, **kwargs):
            yield chunk

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        if isinstance(response, ModelResponse):
            return response
        return ModelResponse(role="assistant", content=str(response))

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return self._parse_provider_response(response)

    @staticmethod
    def _last_user_message(messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user" and message.content is not None:
                return str(message.content)
        return ""

    @staticmethod
    def _echo_with_history(messages: list[Message]) -> str:
        visible = [
            f"{message.role}:{message.content}"
            for message in messages
            if message.role in {"user", "assistant", "tool"} and message.content
        ]
        return " | ".join(visible[-4:])

    @staticmethod
    def _chunks(content: str, size: int = 18) -> Iterator[str]:
        for index in range(0, len(content), size):
            yield content[index : index + size]


def build_agent(db_file: Path, session_id: str, user_id: str) -> Agent:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return Agent(
        id="agno-spike-agent",
        name="Agno Spike Agent",
        model=OfflineEchoModel(
            id="offline-echo",
            name="Offline Echo",
            provider="local deterministic model",
        ),
        db=SqliteDb(db_file=str(db_file)),
        tools=[add_numbers],
        user_id=user_id,
        session_id=session_id,
        add_history_to_context=True,
        num_history_runs=3,
    )


def print_run(label: str, run_output: Any) -> None:
    print(f"\n[{label}]")
    print(f"run_id={run_output.run_id}")
    print(f"session_id={run_output.session_id}")
    print(f"content={run_output.content}")
    if run_output.tools:
        for tool in run_output.tools:
            print(f"tool={tool.tool_name} result={tool.result}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a minimal Agno agent outside Bauer.")
    parser.add_argument("--db", default="tmp/agno_spike.db", help="SQLite file used by Agno sessions.")
    parser.add_argument("--session-id", default="agno-spike-session", help="Stable Agno session id.")
    parser.add_argument("--user-id", default="local-user", help="Agno user id.")
    args = parser.parse_args()

    agent = build_agent(Path(args.db), args.session_id, args.user_id)

    first = agent.run("Primeira chamada simples fora do Bauer.")
    print_run("simple", first)

    second = agent.run("Segunda chamada: confirme que existe historico de sessao.")
    print_run("session", second)

    tool_run = agent.run("Use a tool de soma para calcular 2 + 3.")
    print_run("tool", tool_run)

    print("\n[stream]")
    streamed = []
    stream_run_id = None
    for event in agent.run("Streaming simples via Agno SDK.", stream=True):
        stream_run_id = getattr(event, "run_id", stream_run_id)
        content = getattr(event, "content", None)
        if content:
            streamed.append(str(content))
            print(f"chunk={content}")
    print(f"run_id={stream_run_id}")
    print(f"streamed_content={''.join(streamed)}")

    print("\n[storage]")
    print(f"db={Path(args.db).resolve()}")
    print("mode=SDK Python direto")
    print("http_port=none")


if __name__ == "__main__":
    main()
