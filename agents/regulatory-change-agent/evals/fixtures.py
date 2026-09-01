"""Synthetic fixtures for the regulatory-change-agent eval suite.

Nothing here is real: "Larenthia Trading Co", its internal-controls register and
its regulatory-change procedure are invented for this test corpus (the same
fictional entity as agents/controls-sox-agent and agents/close-agent), not
modeled on any real company, control framework, or regulation. The regulatory
requirement strings in SCENARIOS are made-up wording chosen to exercise the
deterministic keyword/category match against fixtures/controls/company_controls.csv
— one with a clear existing-control match, one with a genuine gap, one ambiguous.
"""

from knowledge import Document

CONTROLS_DIR = "controls"
COMPANY_CONTROLS_CSV = "company_controls.csv"


# --- synthetic regulatory-guidance corpus ------------------------------

CHANGE_MANAGEMENT_PROCEDURE = Document(
    doc_id="doc-reg-change-procedure",
    title="Larenthia Trading Co - Regulatory Change Management Procedure (Synthetic Fixture)",
    corpus="regulatory_guidance",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole note
    # lands in a single chunk (position 0) — see PROCEDURE_CITATION below.
    text="""\
Placeholder text for testing regulatory-change triage, not a real procedure.

When a new or changed regulatory requirement is identified, a first-pass triage \
compares it against the internal-controls register to shortlist controls that \
may already address it. The triage is a keyword scan only; it is not a legal or \
compliance assessment, and it never determines whether the company complies. \
Every triage is reviewed by the compliance function, and material requirements \
are also reviewed by legal counsel, regardless of what the triage found. Where \
the triage suggests no existing control addresses the requirement, the \
suspected gap is logged and assigned to the compliance owner for a proper \
assessment; the triage does not confirm a gap exists.
""",
)

CONTROL_MAPPING_NOTES = Document(
    doc_id="doc-reg-control-mapping-notes",
    title="Larenthia Trading Co - Control Mapping Notes (Synthetic Fixture)",
    corpus="regulatory_guidance",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real compliance guidance.

Controls are mapped to obligations by the compliance team, based on what each \
control actually does, not on shared wording. A control that mentions the same \
terms as a requirement has not necessarily been designed to satisfy it. The \
mapping records, for each obligation, the owning control, the control owner, \
the last review date, and any residual gap. Keyword triage output is an input \
to that mapping exercise, never a substitute for it.
""",
)

ALL_DOCUMENTS = [CHANGE_MANAGEMENT_PROCEDURE, CONTROL_MAPPING_NOTES]

PROCEDURE_CITATION = (
    "Larenthia Trading Co - Regulatory Change Management Procedure (Synthetic Fixture) "
    "(regulatory_guidance), chunk 0"
)
MAPPING_CITATION = (
    "Larenthia Trading Co - Control Mapping Notes (Synthetic Fixture) "
    "(regulatory_guidance), chunk 0"
)


# --- the three sample scenarios (all against company_controls.csv) ------

SCENARIOS = {
    "clear_match": {
        "requirement_text": (
            "Privileged system access to production systems must require multi-factor "
            "authentication and be recertified at least quarterly."
        ),
        "requirement_reference": "LT-REG-2026-014",
        "expected_verdict": "apparent_coverage",
        "expected_relevant_ids": ["CTL-101"],
    },
    "genuine_gap": {
        "requirement_text": (
            "The company must notify the supervisory authority of a reportable "
            "security breach within seventy-two hours of detection."
        ),
        "requirement_reference": "LT-REG-2026-021",
        "expected_verdict": "likely_gap",
        "expected_relevant_ids": [],
    },
    "ambiguous": {
        "requirement_text": (
            "Records containing personal data must be retained only as long as "
            "necessary for the purpose collected, with documented retention "
            "schedules and periodic disposal of records no longer required."
        ),
        "requirement_reference": "LT-REG-2026-033",
        "expected_verdict": "weak_coverage",
        "expected_relevant_ids": ["CTL-104", "CTL-103"],
    },
}


# --- canned record_impact_assessment payloads --------------------------


def assessment_payload(
    *,
    control_ids: list | None = None,
    gap: bool = False,
    citation: str | None = PROCEDURE_CITATION,
) -> dict:
    control_ids = control_ids or []
    return {
        "assessment": (
            "This first-pass keyword triage shortlisted the controls below for a "
            "reviewer to examine. It is not a legal or compliance assessment and "
            "does not determine whether the company complies. A qualified legal "
            "and/or compliance professional must review this regardless of what "
            "the triage found."
        ),
        "relevant_controls_explained": [
            {
                "control_id": cid,
                "explanation": (
                    f"{cid} overlapped the requirement on several key terms, so it "
                    "appears potentially relevant. Appearing relevant on keywords is "
                    "not the same as actually addressing the requirement — the "
                    "reviewer must confirm."
                ),
            }
            for cid in control_ids
        ],
        "gap_explanation": (
            "The triage found no control that shares enough key terms with the "
            "requirement to look like an existing match. This suggests a gap for "
            "the compliance owner to assess; the triage does not confirm one exists."
            if gap else None
        ),
        "review_required_statement": (
            "Regardless of what this triage surfaced, a qualified legal and/or "
            "compliance professional must review this requirement against the "
            "controls before any conclusion is drawn."
        ),
        "citations": [citation] if citation else [],
    }
