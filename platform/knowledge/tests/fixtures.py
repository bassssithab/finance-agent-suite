"""Synthetic corpus for tests only.

None of this text is real accounting guidance — it's invented prose that
exercises retrieval (distinct vocabulary per topic, multi-paragraph docs,
one doc long enough to force multi-chunk splitting). Real standard/policy
excerpts are a follow-up (tracked in platform/knowledge/README.md), not a
substitute for this fixture set.
"""

from knowledge import Document

REVENUE_POLICY = Document(
    doc_id="doc-revenue",
    title="Revenue Recognition Policy (Synthetic Fixture)",
    corpus="policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real accounting guidance.

Revenue is recognized when a performance obligation is satisfied by \
transferring a promised good or service to a customer. A contract with a \
customer must have commercial substance before any revenue is recognized.

The transaction price is allocated to each distinct performance obligation \
in the customer contract based on relative standalone selling price.

Revenue from a performance obligation satisfied over time is recognized by \
measuring progress toward complete satisfaction of that obligation.
""",
)

TRAVEL_POLICY = Document(
    doc_id="doc-travel",
    title="Employee Travel & Expense Policy (Synthetic Fixture)",
    corpus="policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not a real company policy.

Employees must submit a travel expense reimbursement request within thirty \
days of trip completion. Every reimbursement request requires an original \
receipt for any expense over twenty-five dollars.

Mileage for personal vehicle use on business travel is reimbursed at the \
standard mileage rate published annually by the finance department.

Airfare should be booked in economy class unless the flight segment \
exceeds six hours, in which case premium economy is reimbursable.
""",
)

LEASE_STANDARD = Document(
    doc_id="doc-leases",
    title="Lease Accounting Summary (Synthetic Fixture)",
    corpus="standard",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real standards text.

A lessee recognizes a right-of-use asset and a lease liability at the \
commencement date of a lease. The lease liability is initially measured at \
the present value of unpaid lease payments, discounted using the rate \
implicit in the lease when that rate is readily determinable.

The right-of-use asset is initially measured at the amount of the lease \
liability, adjusted for lease payments made at or before commencement, \
initial direct costs, and any lease incentives received.

Subsequent to commencement, a lessee generally amortizes the right-of-use \
asset and accretes interest on the lease liability separately, except for \
leases classified as short-term or low-value.

Short-term leases, defined as having a lease term of twelve months or less \
at commencement with no purchase option the lessee is reasonably certain to \
exercise, may be expensed on a straight-line basis instead of recognizing a \
right-of-use asset and lease liability.
""",
)

ALL_DOCUMENTS = [REVENUE_POLICY, TRAVEL_POLICY, LEASE_STANDARD]
