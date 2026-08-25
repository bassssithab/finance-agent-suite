import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from fakes import refusal_response, text_response
from fixtures import ALL_DOCUMENTS, OUT_OF_SCOPE_DROP_SHIPMENT
from knowledge import KnowledgeBase
from vat_treatment_agent import InvoiceLineItem, determine_vat_treatment

TS = iter(f"2026-08-01T00:0{i}:00Z" for i in range(10))

DROP_SHIPMENT_ITEM = InvoiceLineItem(
    goods_type="consumer electronics",
    customer_location="a country other than Larenthia",
    transaction_type="drop-shipped directly from a foreign supplier to the foreign customer",
)


def fixed_now():
    return next(TS)


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


@pytest.fixture
def audit_log(tmp_path):
    store = AuditLogStore(tmp_path / "audit.db")
    yield store
    store.close()


@pytest.fixture
def approval_queue(tmp_path, audit_log):
    queue = ApprovalQueue(tmp_path / "approvals.db", audit_log)
    yield queue
    queue.close()


def test_end_to_end_answer_flows_to_approved(knowledge_base, audit_log, approval_queue):
    client = text_response(
        "This supply is out-of-scope: the goods are shipped directly from the "
        "foreign supplier to the foreign customer and never enter Larenthia "
        '(Out-of-Scope Pass-Through (Drop-Shipment) Supplies (Synthetic Fixture) (vat_policy), chunk 1).'
    )

    run = determine_vat_treatment(
        line_item=DROP_SHIPMENT_ITEM,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is False
    assert "out-of-scope" in run.draft.answer_text
    assert run.draft.chunk_ids  # retrieval actually found something
    assert run.draft.model == "claude-sonnet-5"

    request = run.approval_request
    assert request is not None
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "vat-treatment-agent"
    assert request.payload["answer_text"] == run.draft.answer_text
    assert request.payload["citations"] == run.draft.citations

    # Walk the approval chain: reviewer, then approver.
    reviewed = approval_queue.decide(
        request.id, actor="alice", role=Role.REVIEWER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert reviewed.current_stage == Role.APPROVER.value

    approved = approval_queue.decide(
        request.id, actor="bob", role=Role.APPROVER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert approved.status == "approved"

    # Full audit trail: retrieval, drafting, submission, and both decisions.
    events = audit_log.get_all()
    actions = [e.action for e in events]
    assert actions == [
        "chunks_retrieved",
        "treatment_drafted",
        "approval_submitted:vat_treatment_determination",
        "approval_reviewer_approve:vat_treatment_determination",
        "approval_approver_approve:vat_treatment_determination",
    ]

    retrieved_event = events[0]
    assert retrieved_event.output["chunks"]
    assert retrieved_event.output["chunks"][0]["doc_id"] == OUT_OF_SCOPE_DROP_SHIPMENT.doc_id

    drafted_event = events[1]
    assert drafted_event.model == "claude-sonnet-5"
    assert drafted_event.prompt_hash == run.draft.prompt_hash
    assert drafted_event.output["answer_text"] == run.draft.answer_text

    assert audit_log.verify_chain().ok is True


def test_a_refusal_is_logged_but_never_reaches_approvals(knowledge_base, audit_log, approval_queue):
    client = refusal_response(category="cyber")

    run = determine_vat_treatment(
        line_item=DROP_SHIPMENT_ITEM,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is True
    assert run.approval_request is None
    assert approval_queue.list_pending() == []

    actions = [e.action for e in audit_log.get_all()]
    assert actions == ["chunks_retrieved", "treatment_refused"]
    assert audit_log.verify_chain().ok is True


def test_a_line_item_with_no_dedicated_exempt_document_still_drafts_and_submits(
    knowledge_base, audit_log, approval_queue
):
    # This corpus (see evals/fixtures.py) defines "exempt" only in the
    # general-scope overview doc, with no dedicated exempt-supply example —
    # a known, documented gap (see the plan's Phase 0 note). Retrieval still
    # finds the overview doc, and the model (scripted here, never a real
    # call) is expected to say the context isn't specific enough to classify
    # confidently rather than guess — the same "don't fabricate" behavior as
    # a refusal, but expressed as ordinary drafted text rather than a safety
    # refusal, so it still reaches approvals for a human to resolve.
    bullion_item = InvoiceLineItem(
        goods_type="investment-grade gold bullion",
        customer_location="Larenthia",
        transaction_type="domestic resale",
    )
    client = text_response(
        "The provided context defines the general categories of standard-rated, "
        "zero-rated export, exempt, and out-of-scope supplies "
        "(VAT Code: General Scope & Rates (Synthetic Fixture) (vat_policy), chunk 1), "
        "but does not contain a specific rule for bullion resale, so this cannot be "
        "classified with confidence from the given excerpts alone."
    )

    run = determine_vat_treatment(
        line_item=bullion_item,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        now=fixed_now,
    )

    assert run.draft.refused is False
    assert run.draft.chunk_ids  # the general overview doc is still retrieved
    assert "cannot be classified with confidence" in run.draft.answer_text
    assert run.approval_request is not None
    assert run.approval_request.status == "pending"
