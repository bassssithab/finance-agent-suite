"""Synthetic corpus for this agent's own eval suite, in the style of
platform/knowledge/tests/fixtures.py (kept as a separate file so the two test
suites don't share state).

None of this text is real accounting guidance — it's invented prose that
exercises retrieval and citation. Swap in real ASC/IFRS excerpts before this
agent is used for an actual accounting determination — see
platform/knowledge/README.md's "Deferred" section, which flags the same gap
for the platform-level fixtures.
"""

from knowledge import Document

SOFTWARE_CAPITALIZATION_STANDARD = Document(
    doc_id="doc-software-capitalization",
    title="Capitalization of Internal-Use Software Costs (Synthetic Fixture)",
    corpus="standard",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real standards text.

Costs incurred during the preliminary project stage of internal-use software \
development are expensed as incurred. The preliminary project stage includes \
conceptual formulation of alternatives, evaluation of alternatives, and \
determination of existing technology needed to achieve the performance \
requirements.

Once the application development stage begins, direct costs of materials and \
services consumed, payroll costs for employees directly associated with the \
project, and interest costs incurred while developing the software are \
capitalized. The application development stage begins only after both \
management has authorized and committed to funding the project and the \
preliminary project stage is complete.

Capitalization of internal-use software costs ceases when the software is \
substantially complete and ready for its intended use, including after all \
substantial testing is complete. Costs incurred after that point, such as \
training and data conversion, are expensed as incurred.

Upgrades and enhancements are capitalized only if it is probable that the \
upgrade or enhancement will result in additional functionality. Costs of \
specified upgrades and enhancements that do not add functionality, including \
most maintenance activities, are expensed as incurred.
""",
)

TRAVEL_POLICY = Document(
    doc_id="doc-travel-tax",
    title="Business Travel Reimbursement Policy (Synthetic Fixture)",
    corpus="policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not a real company policy.

Reimbursement requests for business travel must be submitted within forty-five \
days of trip completion, accompanied by itemized receipts for any single \
expense exceeding fifty dollars.

Ground transportation between the traveler's home and the departure airport is \
reimbursable at the standard mileage rate published by the finance \
department, or at actual cost for ride-share and taxi fares with a receipt.
""",
)

ALL_DOCUMENTS = [SOFTWARE_CAPITALIZATION_STANDARD, TRAVEL_POLICY]
