from decimal import Decimal

from knowledge import Chunk, SearchResult

from controls_sox_agent.models import ControlTestResult, Violation
from controls_sox_agent.narrate import (
    RECORD_NARRATIVES_TOOL,
    draft_deficiency_narratives,
)
from fakes import narratives_client, no_tool_call_client, refusal_client
from fixtures import POLICY_CITATION, narratives_payload

TOOL_NAME = "record_deficiency_narratives"


def result(entry_id="JE-1", amount="90000.00", preparer="alice", a1="alice", a2=None):
    return ControlTestResult(
        entry_id=entry_id,
        date="2026-07-01",
        account="6000",
        amount=Decimal(amount),
        currency="USD",
        preparer=preparer,
        approver_1=a1,
        approver_2=a2,
        dual_approval_required=Decimal(amount) >= 50000,
        passed=False,
        violations=[],
    )


def pair(entry_id="JE-1", code="preparer_is_approver"):
    r = result(entry_id=entry_id)
    v = Violation(entry_id=entry_id, code=code, detail=f"{entry_id}: {code}")
    return (r, v)


def chunk(citation_doc="doc-x", position=0, text="Policy: preparer may not approve."):
    c = Chunk(
        chunk_id=f"{citation_doc}:{position}",
        doc_id=citation_doc,
        doc_title="Controls Policy",
        corpus="internal_controls_policy",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def test_tool_schema_requires_one_narrative_per_field():
    props = RECORD_NARRATIVES_TOOL["input_schema"]["properties"]["narratives"]["items"]
    assert set(props["required"]) == {
        "entry_id", "violation_code", "narrative", "citations", "remediation"
    }


def test_grounded_narratives_are_parsed_in_flagged_order():
    flagged = [pair("JE-1", "preparer_is_approver"), pair("JE-2", "missing_second_approver")]
    client = narratives_client(narratives_payload(
        [("JE-1", "preparer_is_approver"), ("JE-2", "missing_second_approver")]
    ))

    out = draft_deficiency_narratives(client, flagged, [chunk()])

    assert [n.entry_id for n in out.narratives] == ["JE-1", "JE-2"]
    assert [n.violation_code for n in out.narratives] == [
        "preparer_is_approver", "missing_second_approver"
    ]
    assert all(n.citations == [POLICY_CITATION] for n in out.narratives)
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    flagged = [pair("JE-1", "no_approver")]
    client = narratives_client(narratives_payload([("JE-1", "no_approver")], citation=None))

    out = draft_deficiency_narratives(client, flagged, [])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.narratives[0].citations == []
    assert "<context>\n(no internal-controls-policy excerpts" in client.messages.last_request["messages"][0]["content"]


def test_refusal_returns_none_narratives():
    out = draft_deficiency_narratives(refusal_client(category="cyber"), [pair()], [chunk()])

    assert out.narratives is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash  # still recorded


def test_missing_tool_call_is_a_parse_error():
    out = draft_deficiency_narratives(no_tool_call_client(), [pair()], [chunk()])

    assert out.narratives is None
    assert out.refused is False
    assert "no record_deficiency_narratives tool call" in out.parse_error


def test_unknown_exception_key_from_model_is_a_parse_error():
    flagged = [pair("JE-1", "preparer_is_approver")]
    client = narratives_client(narratives_payload([("JE-9", "duplicate_approvers")]))

    out = draft_deficiency_narratives(client, flagged, [chunk()])

    assert out.narratives is None
    assert "unknown exception" in out.parse_error


def test_exception_the_model_omitted_is_filled_with_a_placeholder():
    flagged = [pair("JE-1", "preparer_is_approver"), pair("JE-2", "no_approver")]
    client = narratives_client(narratives_payload([("JE-1", "preparer_is_approver")]))

    out = draft_deficiency_narratives(client, flagged, [chunk()])

    assert [n.entry_id for n in out.narratives] == ["JE-1", "JE-2"]
    assert out.narratives[1].narrative == "No narrative returned for this exception."
    assert out.narratives[1].citations == []
