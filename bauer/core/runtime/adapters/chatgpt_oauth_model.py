"""Agno model backed by the Bauer ChatGPT browser OAuth session.

The ChatGPT OAuth token used by ``bauer auth login -p openai`` is not an
OpenAI Platform API key.  Agno's ``OpenAIChat`` therefore cannot use it: that
model always creates the public ``api.openai.com`` client.  This small model
keeps Agno's lifecycle/session handling while delegating the actual request
to Bauer's ``ChatGPTBackendClient`` (Responses API).

The model translates Agno function definitions and tool-call messages to the
ChatGPT Responses API. Agno remains responsible for executing trusted Bauer
callables and sending their results back through the next model turn.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any


def build_chatgpt_oauth_model(client: Any, model_id: str) -> Any:
    """Build an Agno ``Model`` around an authenticated Bauer client.

    Agno is an optional dependency in Bauer, so imports stay inside the
    factory.  This keeps the catalog and native runtime importable without
    installing the Agno extra.
    """

    try:
        from agno.models.base import Model
        from agno.models.message import Message
        from agno.models.response import ModelResponse
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - optional extra
        raise RuntimeError("Agno OAuth model requires the 'agno' package.") from exc

    class ChatGPTOAuthModel(Model):
        supports_native_structured_outputs = False
        supports_native_tools = True

        def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
            converted: list[dict[str, Any]] = []
            for message in messages:
                content = message.get_content()
                if not isinstance(content, str):
                    content = message.get_content_string()
                converted_message: dict[str, Any] = {
                    "role": message.role,
                    "content": content or "",
                }
                if message.tool_calls:
                    converted_message["tool_calls"] = list(message.tool_calls)
                if message.tool_call_id:
                    converted_message["tool_call_id"] = message.tool_call_id
                converted.append(converted_message)
            return converted

        def _events(
            self,
            messages: list[Message],
            tools: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
        ) -> list[dict[str, Any]]:
            event_stream = getattr(client, "chat_stream_events", None)
            if callable(event_stream):
                return list(event_stream(
                    self.id,
                    self._messages(messages),
                    tools=tools,
                    tool_choice=tool_choice,
                ))
            return [
                {"type": "text_delta", "delta": delta}
                for delta in client.chat_stream(self.id, self._messages(messages))
            ]

        @staticmethod
        def _response_from_events(events: list[dict[str, Any]]) -> ModelResponse:
            content = "".join(
                str(event.get("delta") or "")
                for event in events
                if event.get("type") == "text_delta"
            )
            tool_calls = [
                event["tool_call"]
                for event in events
                if event.get("type") == "tool_call" and event.get("tool_call")
            ]
            response = ModelResponse(role="assistant", content=content or None)
            if tool_calls:
                response.tool_calls = tool_calls
            return response

        def invoke(
            self,
            messages: list[Message],
            assistant_message: Message,
            tools: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
            **_: Any,
        ) -> ModelResponse:
            return self._response_from_events(self._events(messages, tools, tool_choice))

        async def ainvoke(
            self,
            messages: list[Message],
            assistant_message: Message,
            tools: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
            **_: Any,
        ) -> ModelResponse:
            return await asyncio.to_thread(
                lambda: self._response_from_events(self._events(messages, tools, tool_choice))
            )

        def invoke_stream(
            self,
            messages: list[Message],
            assistant_message: Message,
            tools: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
            **_: Any,
        ) -> Iterator[ModelResponse]:
            for event in self._events(messages, tools, tool_choice):
                if event.get("type") == "text_delta" and event.get("delta"):
                    yield ModelResponse(role="assistant", content=str(event["delta"]))
                elif event.get("type") == "tool_call" and event.get("tool_call"):
                    yield ModelResponse(role="assistant", tool_calls=[event["tool_call"]])

        async def ainvoke_stream(
            self,
            messages: list[Message],
            assistant_message: Message,
            tools: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
            **_: Any,
        ) -> AsyncIterator[ModelResponse]:
            events = await asyncio.to_thread(lambda: self._events(messages, tools, tool_choice))
            for event in events:
                if event.get("type") == "text_delta" and event.get("delta"):
                    yield ModelResponse(role="assistant", content=str(event["delta"]))
                elif event.get("type") == "tool_call" and event.get("tool_call"):
                    yield ModelResponse(role="assistant", tool_calls=[event["tool_call"]])

        def _parse_provider_response(self, response: Any, **_: Any) -> ModelResponse:
            if isinstance(response, ModelResponse):
                return response
            return ModelResponse(role="assistant", content=str(response or ""))

        def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
            return self._parse_provider_response(response)

    return ChatGPTOAuthModel(
        id=model_id,
        name="Bauer ChatGPT OAuth",
        provider="Bauer browser OAuth",
    )
