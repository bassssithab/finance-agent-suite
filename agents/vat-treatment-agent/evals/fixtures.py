"""Synthetic VAT-policy corpus for this agent's own eval suite, in the style
of agents/technical-accounting-agent/evals/fixtures.py.

None of this text is real VAT law — it describes a fictional jurisdiction
("Larenthia") invented for this test corpus, co-written with the project
maintainer, and is not modeled on any real country's tax code or any real
company. It exists purely to exercise retrieval, citation, and the
exempt-vs-out-of-scope distinction the agent must draw correctly.
"""

from knowledge import Document

VAT_SCOPE_AND_RATES = Document(
    doc_id="doc-vat-scope-rates",
    title="VAT Code: General Scope & Rates (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

A supply of goods by a VAT-registered trading business falls into one of \
four categories: standard-rated, zero-rated (export), exempt, or \
out-of-scope. Standard-rated and zero-rated supplies are taxable supplies \
within the scope of the VAT Code; input VAT incurred on related purchases \
is recoverable for both.

Exempt supplies fall within the scope of the VAT Code but are specifically \
relieved from VAT by its provisions; input VAT incurred on related \
purchases is not recoverable.

Out-of-scope supplies do not fall within the territorial or transactional \
scope of the VAT Code at all — most commonly because the goods never enter \
Larenthia — and are therefore governed by none of its rate or recovery \
provisions. A business should not describe an out-of-scope supply as \
"exempt": the two are distinct legal categories with different consequences \
for record-keeping and cross-border reporting.
""",
)

STANDARD_RATED_DOMESTIC_SALES = Document(
    doc_id="doc-vat-standard-domestic",
    title="Standard-Rated Domestic Sales (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

A sale of goods by a VAT-registered reseller to a customer located within \
Larenthia is standard-rated at 15%, charged at the point of sale, unless \
the goods or transaction qualify for zero-rating, exemption, or \
out-of-scope treatment under other provisions of the VAT Code. This is the \
default treatment for domestic trading activity.
""",
)

ZERO_RATED_EXPORTS = Document(
    doc_id="doc-vat-zero-rated-export",
    title="Zero-Rated Exports (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

A sale of goods to a customer located outside Larenthia is zero-rated, \
provided the goods physically leave Larenthian customs territory within 60 \
days of the invoice date and the seller retains proof of export (a customs \
export declaration and carrier waybill) in its records. VAT is charged at \
0%; input VAT on related purchases remains fully recoverable.

If proof of export is not obtained within 60 days, the supply reverts to \
standard-rated and VAT is due at 15% from the original invoice date.
""",
)

OUT_OF_SCOPE_DROP_SHIPMENT = Document(
    doc_id="doc-vat-out-of-scope-dropship",
    title="Out-of-Scope Pass-Through (Drop-Shipment) Supplies (Synthetic Fixture)",
    corpus="vat_policy",
    metadata={"framework": "synthetic", "period": "FY2026"},
    text="""\
This is placeholder text for testing retrieval, not real VAT law.

A sale of goods is outside the scope of Larenthian VAT where the reseller \
purchases the goods from a supplier located outside Larenthia and sells \
them on to a customer located in a different country, and the goods are \
shipped directly from the foreign supplier to the foreign customer without \
at any point entering Larenthia — no import into Larenthia, no local \
customs clearance, and no local warehousing or handling by the reseller. \
Because the goods never enter Larenthian territory, the supply does not \
fall within the territorial scope of the VAT Code at all. No VAT is charged.

This must not be confused with an exempt supply: an exempt supply falls \
within the scope of the VAT Code but is specifically relieved from tax, \
whereas an out-of-scope supply never enters the VAT system in the first \
place. Input VAT recovery is not applicable, since no Larenthian \
VAT-bearing costs are incurred.

Resellers should retain evidence of the drop-shipment routing — the \
foreign supplier's invoice and shipping documentation showing direct \
delivery to the foreign customer — to support out-of-scope treatment.
""",
)

ALL_DOCUMENTS = [
    VAT_SCOPE_AND_RATES,
    STANDARD_RATED_DOMESTIC_SALES,
    ZERO_RATED_EXPORTS,
    OUT_OF_SCOPE_DROP_SHIPMENT,
]
