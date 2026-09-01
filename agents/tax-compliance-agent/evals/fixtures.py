"""Synthetic VAT corpus for this agent's eval suite.

None of this text is real VAT law — it describes the same fictional jurisdiction
("Larenthia") that agents/vat-treatment-agent classifies against. It is
**deliberately consistent** with agents/vat-treatment-agent/evals/fixtures.py:
the same four VAT treatment categories (standard-rated, zero-rated, exempt,
out-of-scope), the same 15% standard rate, and the same input-VAT recovery
rules. Those shared rules are *restated* here rather than imported, so this
agent's folder stays self-contained (CLAUDE.md rule #5, and the repo pattern
where every agent's evals/fixtures.py is standalone). The filing-and-provision
documents (VAT_RETURN_FILING, SPECIALIST_REVIEW_TRIGGERS) are new to this agent.

The transaction CSVs under fixtures/transactions/ are fictional rows chosen to
exercise the provision arithmetic and the anomaly checks — a normal payable
position, a refundable position, and a batch with data-quality problems.
"""

from knowledge import Document

TRANSACTIONS_DIR = "transactions"


# --- shared Larenthia VAT rules (consistent with vat-treatment-agent) ----

VAT_SCOPE_RATES_RECOVERY = Document(
    doc_id="doc-vat-scope-rates-recovery",
    title="VAT Code: Scope, Rates & Input Recovery (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

A supply by a VAT-registered trading business falls into one of four \
categories: standard-rated, zero-rated (export), exempt, or out-of-scope. A \
standard-rated supply is taxed at 15%. Zero-rated, exempt and out-of-scope \
supplies bear no VAT. Input VAT incurred on purchases is recoverable where the \
purchase supports standard-rated or zero-rated supplies; it is not recoverable \
on exempt-related purchases, and recovery does not arise for out-of-scope \
activity. Exempt and out-of-scope are distinct legal categories and must not be \
used interchangeably.
""",
)

VAT_RETURN_FILING = Document(
    doc_id="doc-vat-return-filing",
    title="VAT Code: Period Return & Provision (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

At period end the business computes output VAT on its sales and input VAT on \
its purchases and nets them: a net amount owed to the tax authority is a \
payable position, and a net amount owed to the business is a refundable \
position. A refundable position is reviewed before the return is submitted, \
because for a business whose sales are mostly standard-rated it is unusual. A \
return that still contains a transaction the system could not classify, a \
treatment that is not one of the four categories, or a mismatch between a \
transaction's treatment and its rate is not filed as-is: the item is referred \
to the tax function for resolution first. The analyst prepares the provision, \
the tax lead reviews it, and the return is filed by a person qualified to do so.
""",
)

SPECIALIST_REVIEW_TRIGGERS = Document(
    doc_id="doc-vat-specialist-review",
    title="VAT Code: When to Involve a Tax Specialist (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT guidance.

The following are referred to a qualified tax specialist before the return is \
filed, not resolved by the preparer: a net refundable position; any transaction \
whose VAT treatment is blank or outside the four recognised categories; a \
standard-rated transaction with no rate or a zero-rated transaction carrying a \
rate; and a material volume of out-of-scope cross-border activity. The \
preparer's job is to surface these clearly in the filing-support notes, not to \
decide how they are treated or whether the return is correct.
""",
)

ALL_DOCUMENTS = [
    VAT_SCOPE_RATES_RECOVERY,
    VAT_RETURN_FILING,
    SPECIALIST_REVIEW_TRIGGERS,
]

# Citation labels the KnowledgeBase produces for the (single) chunk of each doc.
SCOPE_CITATION = (
    "VAT Code: Scope, Rates & Input Recovery (Synthetic Fixture) (vat_policy), chunk 0"
)
FILING_CITATION = (
    "VAT Code: Period Return & Provision (Synthetic Fixture) (vat_policy), chunk 0"
)
SPECIALIST_CITATION = (
    "VAT Code: When to Involve a Tax Specialist (Synthetic Fixture) (vat_policy), chunk 0"
)


# --- canned record_filing_support_narrative payloads --------------------


def narrative_payload(
    *,
    anomaly_codes: list | None = None,
    specialist_review: bool = False,
    citation: str | None = FILING_CITATION,
) -> dict:
    anomaly_codes = anomaly_codes or []
    return {
        "position_summary": (
            "Under the deterministic calculation the period shows output VAT on "
            "sales netted against input VAT on purchases, giving the net position "
            "recorded above. This is the calculation's result for the reviewer, "
            "not a confirmed or final return."
        ),
        "anomaly_explanations": [
            f"{code}: the deterministic check flagged this; it is described in the "
            "provision and, per the notes below, is referred for review rather than "
            "resolved here."
            for code in anomaly_codes
        ],
        "specialist_review_needed": specialist_review,
        "citations": [citation] if citation else [],
    }
