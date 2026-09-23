"""Agno model backed by the Bauer ChatGPT browser OAuth session.

The ChatGPT OAuth token used by ``bauer auth login -p openai`` is not an
OpenAI Platform API key.  Agno's ``OpenAIChat`` therefore cannot use it: that
model always creates the public ``api.openai.com`` client.  This small model
keeps Agno's lifecycle/session handling while delegating the actual request
to Bauer's ``ChatGPTBackendClient`` (Responses API).

The ChatGPT backend currently exposes text deltas only.  Consequently this
model deliberately disables native tool calling; Bauer tools remain available
through the native Bauer executor until Responses tool-call translation is
implemented.
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
        supports_native_tools = False

        def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
            converted: list[dict[str, Any]] = []
            for message in messages:
                content = message.get_content()
                if not isinstance(content, str):
                    content = message.get_content_string()
                converted.append({"role": message.role, "content": content or ""})
            return converted

        def _stream(self, messages: list[Message]) -> Iterator[str]:
            yield from client.chat_stream(self.id, self._messages(messages))

        def invoke(
            self,
            messages: list[Message],
            assistant_message: Message,
            **_: Any,
        ) -> ModelResponse:
            content = "".join(self._stream(messages))
            return ModelResponse(role="assistant", content=content)

        async def ainvoke(
            self,
            messages: list[Message],
            assistant_message: Message,
            **_: Any,
        ) -> ModelResponse:
            content = await asyncio.to_thread(lambda: "".join(self._stream(messages)))
            return ModelResponse(role="assistant", content=content)

        def invoke_stream(
            self,
            messages: list[Message],
            assistant_message: Message,
            **_: Any,
        ) -> Iterator[ModelResponse]:
            for delta in self._stream(messages):
                yield ModelResponse(role="assistant", content=delta)

        async def ainvoke_stream(
            self,
            messages: list[Message],
            assistant_message: Message,
            **_: Any,
        ) -> AsyncIterator[ModelResponse]:
            chunks = await asyncio.to_thread(lambda: list(self._stream(messages)))
            for delta in chunks:
                yield ModelResponse(role="assistant", content=delta)

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
