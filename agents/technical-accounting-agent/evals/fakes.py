"""Fake Anthropic client for tests — no network calls, no ANTHROPIC_API_KEY.

Tests inject one of these in place of a real anthropic.Anthropic() client, so
they exercise the exact same client.messages.create(...) call shape that
technical_accounting_agent.llm.draft_answer makes, without depending on the
anthropic package being installed or costing real API tokens.
"""

from dataclasses import dataclass, field
from typing import Optional


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
    stop_reason: str = "end_turn"
    stop_details: Optional[FakeStopDetails] = None


class FakeMessagesResource:
    def __init__(self, response: FakeMessage):
        self._response = response
        self.last_request: Optional[dict] = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return self._response


class FakeAnthropicClient:
    def __init__(self, response: FakeMessage):
        self.messages = FakeMessagesResource(response)


def text_response(text: str) -> FakeAnthropicClient:
    return FakeAnthropicClient(FakeMessage(content=[FakeTextBlock(text=text)]))


def refusal_response(category: Optional[str] = "cyber") -> FakeAnthropicClient:
    return FakeAnthropicClient(
        FakeMessage(content=[], stop_reason="refusal", stop_details=FakeStopDetails(category=category))
    )
