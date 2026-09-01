"""Fake Anthropic client for tests — no network calls, no ANTHROPIC_API_KEY.

Same idea as agents/close-agent/evals/fakes.py: fpa-agent's one LLM call
(narrative drafting) forces a `record_forecast_narrative` tool call, so the fake
returns a `tool_use` block and records every request for assertions.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict
    id: str = "toolu_fake"
    type: str = "tool_use"


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeStopDetails:
    category: Optional[str] = None


@dataclass
class FakeMessage:
    content: list
    stop_reason: str = "tool_use"
    stop_details: Optional[FakeStopDetails] = None


class FakeMessagesResource:
    def __init__(self, response: FakeMessage):
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs) -> FakeMessage:
        self.calls.append(kwargs)
        return self._response

    @property
    def last_request(self) -> Optional[dict]:
        return self.calls[-1] if self.calls else None


class FakeAnthropicClient:
    def __init__(self, response: FakeMessage):
        self.messages = FakeMessagesResource(response)


class ExplodingClient:
    """A client that must never be called — the caller raises before the LLM."""

    class _Messages:
        def create(self, **kwargs):
            raise AssertionError("the model must not be called on this path")

    def __init__(self):
        self.messages = self._Messages()


def narrative_client(payload: dict) -> FakeAnthropicClient:
    return FakeAnthropicClient(FakeMessage(
        content=[FakeToolUseBlock(name="record_forecast_narrative", input=payload)],
        stop_reason="tool_use",
    ))


def refusal_client(category: Optional[str] = "cyber") -> FakeAnthropicClient:
    return FakeAnthropicClient(FakeMessage(
        content=[], stop_reason="refusal", stop_details=FakeStopDetails(category=category),
    ))


def no_tool_call_client() -> FakeAnthropicClient:
    return FakeAnthropicClient(FakeMessage(
        content=[FakeTextBlock(text="Here is my forecast view in prose.")], stop_reason="end_turn",
    ))
