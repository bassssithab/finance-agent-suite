"""Synthetic fixtures for the controls-sox-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its internal-controls policy
are invented for this test corpus (the same fictional entity as
agents/close-agent, agents/ap-agent and agents/vat-treatment-agent), not modeled
on any real company, policy manual, or control framework. The journal-entry CSVs
under fixtures/journal_entries/ are fictional rows chosen to exercise the
segregation-of-duties rules — clean entries, self-approval, a missing second
approver above the threshold, duplicate approvers, an unapproved entry, and a
name that differs only by case/whitespace.
"""

from knowledge import Document

JOURNAL_ENTRIES_DIR = "journal_entries"


# --- synthetic internal-controls-policy corpus -----------------------------

JE_CONTROLS_POLICY = Document(
    doc_id="doc-sox-je-controls-policy",
    title="Larenthia Trading Co - Journal Entry Controls Policy (Synthetic Fixture)",
    corpus="internal_controls_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole policy
    # lands in a single chunk (position 0) — see POLICY_CITATION below.
    text="""\
Placeholder text for testing journal-entry controls, not a real controls policy.

Every manual journal entry must be prepared by one person and approved by a \
different person; the preparer may never approve their own entry. A journal \
entry with an absolute value of 50,000 dollars or more requires a second \
approval from a third person, distinct from both the preparer and the first \
approver. An entry recorded without the approvals required for its value is a \
control exception and must be logged in the deficiency register with the entry \
id, the preparer and approver names, and a remediation owner. Whether an \
exception is a deficiency, a significant deficiency, or a material weakness is \
assessed later by the controls owner and the external auditor, not by the \
preparer of this log.
""",
)

APPROVAL_WORKFLOW_NOTE = Document(
    doc_id="doc-sox-approval-workflow-note",
    title="Larenthia Trading Co - Approval Workflow Notes (Synthetic Fixture)",
    corpus="internal_controls_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real control guidance.

Journal-entry approvals are captured in the ERP workflow tool, which stamps the \
approver id and time. Where the tool is bypassed (manual upload, period-end \
adjustments), approvals are evidenced by signed close checklists. Remediation \
for a segregation-of-duties exception usually means re-performing the approval \
by an independent reviewer and reviewing the preparer's system access.
""",
)

ALL_DOCUMENTS = [JE_CONTROLS_POLICY, APPROVAL_WORKFLOW_NOTE]

# Citation label the KnowledgeBase produces for the (single) chunk of the
# controls policy above. chunk_document packs the whole doc into one chunk
# (position 0).
POLICY_CITATION = (
    "Larenthia Trading Co - Journal Entry Controls Policy (Synthetic Fixture) "
    "(internal_controls_policy), chunk 0"
)
WORKFLOW_NOTE_CITATION = (
    "Larenthia Trading Co - Approval Workflow Notes (Synthetic Fixture) "
    "(internal_controls_policy), chunk 0"
)


# --- canned record_deficiency_narratives payloads (what the model would return) --


def narrative_entry(entry_id: str, violation_code: str, citation: str | None = None) -> dict:
    return {
        "entry_id": entry_id,
        "violation_code": violation_code,
        "narrative": (
            f"The segregation-of-duties control over journal-entry approval was not "
            f"operating for entry {entry_id}: the deterministic test flagged "
            f"'{violation_code}'. The approval evidence on file does not show an "
            f"independent approver as the control requires."
            + (f" See {citation}." if citation else "")
        ),
        "citations": [citation] if citation else [],
        "remediation": ["re-perform approval by an independent reviewer"],
    }


def narratives_payload(
    flagged_keys: list[tuple[str, str]], citation: str | None = POLICY_CITATION
) -> dict:
    return {
        "narratives": [
            narrative_entry(entry_id, code, citation) for entry_id, code in flagged_keys
        ]
    }
