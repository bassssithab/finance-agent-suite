"""Fake Anthropic client for tests — no network calls, no ANTHROPIC_API_KEY.

Same idea as agents/ap-agent/evals/fakes.py: this agent's LLM calls (receipt
extraction, policy-explanation drafting) force a tool call, so the fake returns
`tool_use` blocks. The client dispatches on the forced tool name, so a single
fake can serve both calls in one end-to-end run regardless of order, and records
every request for assertions.
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
    def __init__(self, by_tool: dict, default: Optional[FakeMessage] = None):
        self._by_tool = by_tool
        self._default = default
        self.calls: list[dict] = []

    def create(self, **kwargs) -> FakeMessage:
        self.calls.append(kwargs)
        tools = kwargs.get("tools") or []
        for tool in tools:
            if tool["name"] in self._by_tool:
                return self._by_tool[tool["name"]]
        if self._default is not None:
            return self._default
        raise AssertionError(f"fake client has no scripted response for tools={tools!r}")

    @property
    def last_request(self) -> Optional[dict]:
        return self.calls[-1] if self.calls else None


class FakeAnthropicClient:
    def __init__(self, by_tool: Optional[dict] = None, default: Optional[FakeMessage] = None):
        self.messages = FakeMessagesResource(by_tool or {}, default)


def _tool_message(name: str, tool_input: dict) -> FakeMessage:
    return FakeMessage(content=[FakeToolUseBlock(name=name, input=tool_input)], stop_reason="tool_use")


def receipt_client(receipt_payload: dict, explanation_payload: Optional[dict] = None) -> FakeAnthropicClient:
    """Fake that answers record_receipt (and, optionally, record_policy_explanations)."""
    by_tool = {"record_receipt": _tool_message("record_receipt", receipt_payload)}
    if explanation_payload is not None:
        by_tool["record_policy_explanations"] = _tool_message(
            "record_policy_explanations", explanation_payload
        )
    return FakeAnthropicClient(by_tool=by_tool)


def explanation_client(explanation_payload: dict) -> FakeAnthropicClient:
    return FakeAnthropicClient(
        by_tool={"record_policy_explanations": _tool_message("record_policy_explanations", explanation_payload)}
    )


def receipt_then_explanation_refusal(receipt_payload: dict, category: str = "cyber") -> FakeAnthropicClient:
    """Extraction succeeds; the explanation call comes back as a safety refusal."""
    return FakeAnthropicClient(
        by_tool={"record_receipt": _tool_message("record_receipt", receipt_payload)},
        default=FakeMessage(content=[], stop_reason="refusal", stop_details=FakeStopDetails(category=category)),
    )


def receipt_then_explanation_no_tool(receipt_payload: dict) -> FakeAnthropicClient:
    """Extraction succeeds; the explanation call answers in prose, not a tool call."""
    return FakeAnthropicClient(
        by_tool={"record_receipt": _tool_message("record_receipt", receipt_payload)},
        default=FakeMessage(content=[FakeTextBlock(text="Here is my view...")], stop_reason="end_turn"),
    )


def refusal_client(category: Optional[str] = "cyber") -> FakeAnthropicClient:
    return FakeAnthropicClient(
        default=FakeMessage(content=[], stop_reason="refusal", stop_details=FakeStopDetails(category=category))
    )


def no_tool_call_client() -> FakeAnthropicClient:
    """Model answered in prose instead of calling the forced tool."""
    return FakeAnthropicClient(
        default=FakeMessage(content=[FakeTextBlock(text="I can't help with that.")], stop_reason="end_turn")
    )
