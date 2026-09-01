"""Synthetic fixtures for the fpa-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its FP&A methodology are
invented for this test corpus (the same fictional entity as agents/close-agent,
agents/ap-agent and agents/vat-treatment-agent), not modeled on any real company
or methodology manual. The historical-actuals CSVs under fixtures/actuals/ are
fictional monthly figures (2026-04 .. 2026-06) chosen to exercise the projection
rules — a mild upward trend, and a line ("Software subscriptions") that drops
out of the most recent month so the carry-forward / stale-base path is covered.
"""

from knowledge import Document

ACTUALS_DIR = "actuals"


# --- synthetic FP&A-methodology corpus ----------------------------------

FPA_METHODOLOGY = Document(
    doc_id="doc-fpa-methodology-larenthia-trading",
    title="Larenthia Trading Co - Driver-Based Forecasting Methodology (Synthetic Fixture)",
    corpus="fpa_methodology",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole note
    # lands in a single chunk (position 0) — see METHODOLOGY_CITATION below.
    text="""\
Placeholder text for testing forecast methodology, not a real FP&A manual.

A driver-based forecast projects each general-ledger line forward from its most \
recent actual using an explicit growth assumption per category. Every growth \
assumption is owned by a named driver owner and is documented before the \
forecast is circulated. An assumption that implies a period-over-period change \
of 25 percent or more is reviewed with the driver owner and the FP&A lead \
before the forecast is used. The forecast is presented as a projection under \
stated assumptions, not as a target or a commitment; the narrative states the \
assumptions as assumptions and does not describe projected figures as \
outcomes. A line with no actual in the base period is carried forward from its \
last actual and noted as an estimate.
""",
)

FORECAST_REVIEW_NOTE = Document(
    doc_id="doc-fpa-review-note",
    title="Larenthia Trading Co - Forecast Review Notes (Synthetic Fixture)",
    corpus="fpa_methodology",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
Placeholder text for testing retrieval, not real FP&A guidance.

Forecast review focuses on the assumptions, not the arithmetic: the projection \
math is deterministic and is not re-checked line by line. The reviewer decides \
whether each assumed growth rate is supportable given the pipeline, headcount \
plan and price actions, and whether the flagged high-sensitivity lines carry \
enough evidence. Whether a forecast is reasonable, achievable or prudent is the \
reviewer's and the CFO's call, recorded in the approval, not the analyst's.
""",
)

ALL_DOCUMENTS = [FPA_METHODOLOGY, FORECAST_REVIEW_NOTE]

# Citation label the KnowledgeBase produces for the (single) chunk of the
# methodology note above. chunk_document packs the whole doc into one chunk
# (position 0).
METHODOLOGY_CITATION = (
    "Larenthia Trading Co - Driver-Based Forecasting Methodology (Synthetic Fixture) "
    "(fpa_methodology), chunk 0"
)
REVIEW_NOTE_CITATION = (
    "Larenthia Trading Co - Forecast Review Notes (Synthetic Fixture) "
    "(fpa_methodology), chunk 0"
)


# --- canned record_forecast_narrative payloads (what the model would return) --


def narrative_payload(
    *,
    flagged_line_items: list | None = None,
    citation: str | None = METHODOLOGY_CITATION,
) -> dict:
    flagged_line_items = flagged_line_items or []
    return {
        "summary": (
            "Under the stated assumptions the forecast projects a modest upward "
            "trajectory over the horizon. These figures are projections, not "
            "outcomes, and depend on the growth assumptions holding."
        ),
        "assumptions_described": [
            "The plan assumes a flat default growth rate per period for lines "
            "without a category-specific assumption.",
            "Category growth rates are assumptions supplied by the planner, not "
            "measured trends.",
        ],
        "flagged_items_called_out": [
            f"{item}: the assumed rate is large, so a small change in the "
            "assumption moves the forecast materially - the reviewer should scrutinise it."
            for item in flagged_line_items
        ],
        "citations": [citation] if citation else [],
    }
