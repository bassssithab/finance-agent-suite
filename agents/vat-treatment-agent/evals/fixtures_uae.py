"""UAE VAT knowledge corpus — DRAFT, secondary sources, PRIVATE/LOCAL USE ONLY.

======================================================================
DO NOT WIRE THIS INTO app.py, manual_live_run.py, OR ANY TEST.
======================================================================

This is a SEPARATE corpus from the fictional "Larenthia" corpus in
evals/fixtures.py that powers the public Streamlit demo and the eval suite.
Everything here describes REAL UAE VAT and is intended only as a private, local
reference for the maintainer until it has been independently verified.

Status
------
Researched from SECONDARY sources (accounting-firm guides, tax-advisory sites),
current as of September 2026 web research. NOT yet checked against the FTA's own
primary Executive Regulations or Public Clarifications. Treat as a well-informed
first draft, not a citable legal source. Spot-check every document below against
the actual FTA publications (federaltaxauthority.gov.ae) — or have someone with
direct FTA access verify it — before relying on it for anything.

Primary instruments these notes summarise (unverified):
  - Federal Decree-Law No. 8 of 2017 (UAE VAT Law)
  - Cabinet Resolution No. 52 of 2017 (Executive Regulations)
  - Cabinet Decision No. 59 of 2017 (Designated Zones), as amended
  - FTA Public Clarification VATP044 (imported services)
  - Federal Decree-Law No. 16 of 2025 (2026 self-invoicing change)

Why it is kept out of the demo
------------------------------
The public demo's corpus is deliberately fictional so a wrong answer harms
nobody. This corpus answers real questions about a real tax system; an
unverified wrong answer here could mislead someone. It stays local until
verified.

Isolation
---------
`corpus="vat_policy_uae"` — distinct from the demo's `"vat_policy"` so the two
can never be ingested into the same KnowledgeBase by accident. The document list
is `ALL_DOCUMENTS_UAE`, NOT `ALL_DOCUMENTS`, so app.py's `_load_fixture_module`
loader (which reads `.ALL_DOCUMENTS`) cannot pick this up even if pointed here.
Nothing in this repo imports this module.

Out of scope for this pass (add later only if genuinely needed): exempt supplies
in depth (local passenger transport, bare land, residential real estate, certain
financial services), Transfer of a Going Concern, VAT grouping, bad-debt relief,
input-tax recovery restrictions (entertainment, certain vehicles), the
profit-margin scheme, and any interaction with UAE Corporate Tax.
"""

from knowledge import Document

_METADATA = {
    "jurisdiction": "AE",
    "status": "secondary-source-draft",
    "verified_against_primary_law": "no",
    "as_of": "2026-09",
}


UAE_VAT_SCOPE_AND_RATES = Document(
    doc_id="doc-uae-vat-scope-rates",
    title="UAE VAT: General Scope & Rates (secondary-source draft — NOT FTA-verified)",
    corpus="vat_policy_uae",
    metadata=_METADATA,
    text="""\
UAE VAT is governed by Federal Decree-Law No. 8 of 2017 and its Executive \
Regulations (Cabinet Resolution No. 52 of 2017). The standard rate is 5%. \
Mandatory VAT registration applies where a business's taxable supplies and \
imports exceed AED 375,000 over the previous 12 months, or are expected to \
exceed that threshold in the next 30 days.

A supply of goods or services falls into one of four categories: standard-rated \
(5%), zero-rated (0%, input VAT recoverable), exempt (no VAT charged, input VAT \
not recoverable), or out-of-scope (the transaction does not fall within UAE VAT \
territory at all — most commonly because the goods never entered the UAE, or \
fall within a Designated Zone carve-out). Exempt and out-of-scope are legally \
distinct: an exempt supply is within the VAT system but relieved from tax; an \
out-of-scope supply never enters the VAT system in the first place.
""",
)


UAE_STANDARD_RATED_DOMESTIC_SALES = Document(
    doc_id="doc-uae-vat-standard-domestic",
    title="UAE VAT: Standard-Rated Domestic Sales (secondary-source draft — NOT FTA-verified)",
    corpus="vat_policy_uae",
    metadata=_METADATA,
    text="""\
A sale of goods or services by a UAE VAT-registered business to a customer \
located in mainland UAE is standard-rated at 5%, unless the goods, transaction, \
or zone status qualify for zero-rating, exemption, or out-of-scope treatment \
under other provisions of the VAT Law. This is the default treatment for \
ordinary domestic trading activity.
""",
)


UAE_ZERO_RATED_EXPORTS = Document(
    doc_id="doc-uae-vat-zero-rated-export",
    title="UAE VAT: Zero-Rated Exports (secondary-source draft — NOT FTA-verified)",
    corpus="vat_policy_uae",
    metadata=_METADATA,
    text="""\
A sale of goods to a customer located outside the UAE is zero-rated, provided \
the exporter retains evidence that the goods physically left UAE territory \
(customs export declaration, shipping/airway documentation) within the \
timeframe specified in the Executive Regulations. VAT is charged at 0%; input \
VAT on related purchases remains fully recoverable. Certain services supplied \
to non-UAE-resident recipients may also qualify for zero-rating under specific \
conditions in the Executive Regulations.
""",
)


UAE_DESIGNATED_ZONES = Document(
    doc_id="doc-uae-vat-designated-zones",
    title="UAE VAT: Designated Zones — Goods vs. Services (secondary-source draft — NOT FTA-verified)",
    corpus="vat_policy_uae",
    metadata=_METADATA,
    text="""\
A Designated Zone is a specific, Cabinet-approved geographic area (Cabinet \
Decision No. 59 of 2017 and amendments) that meets strict FTA conditions: a \
physical fenced perimeter, monitored security and customs entry/exit points, \
and documented internal procedures for managing goods. Not every UAE free zone \
is a Designated Zone — the list is specific and has been amended multiple \
times; always confirm current status against the latest FTA publication before \
applying this treatment to a real transaction.

Goods supplied within a Designated Zone, or transferred between two Designated \
Zones under proper customs-suspension documentation, may be treated as outside \
the scope of UAE VAT. Goods moving from a Designated Zone into UAE mainland are \
treated as an import into the UAE and trigger VAT under the reverse charge \
mechanism, payable by the mainland recipient.

Critically: services supplied within or from a Designated Zone are always \
subject to standard UAE VAT rules, regardless of zone status. The Designated \
Zone concept only affects the VAT treatment of goods, never services. A \
business operating in a Designated Zone must still register for VAT, file \
returns, and charge standard VAT on any services it supplies.
""",
)


UAE_REVERSE_CHARGE_IMPORTS = Document(
    doc_id="doc-uae-vat-reverse-charge-imports",
    title="UAE VAT: Reverse Charge Mechanism on Imports (secondary-source draft — NOT FTA-verified)",
    corpus="vat_policy_uae",
    metadata=_METADATA,
    text="""\
Under Article 48 of the VAT Law, when a UAE VAT-registered business imports \
goods or services from a supplier outside the UAE (who is not UAE VAT-registered \
and cannot charge UAE VAT), the responsibility for accounting for VAT shifts to \
the UAE-based recipient. The recipient self-assesses VAT at the standard rate, \
declares it as output VAT in their VAT return, and — if otherwise eligible — \
simultaneously reclaims the same amount as input VAT in the same return, \
typically resulting in a net-nil cash impact, though the transaction must still \
be correctly reported.

As of 1 January 2026, UAE VAT-registered businesses are no longer required to \
issue a self-invoice for reverse-charge imports (a requirement removed under \
Federal Decree-Law No. 16 of 2025) — though the underlying obligation to \
self-account for the VAT in the return remains. Failing to report a \
reverse-charge transaction at all — even where it nets to zero — is treated as \
an incorrect tax return and can attract FTA penalties.

The reverse charge mechanism also applies, separately, to specified domestic \
B2B transactions between two UAE VAT-registered businesses in designated \
high-risk sectors (for example, certain wholesale supplies of hydrocarbons, and \
specified electronic devices such as mobile phones, computers, tablets, and \
their essential components, when acquired for resale or manufacturing) — a \
narrower anti-fraud rule distinct from the import-reverse-charge rule above.
""",
)


ALL_DOCUMENTS_UAE = [
    UAE_VAT_SCOPE_AND_RATES,
    UAE_STANDARD_RATED_DOMESTIC_SALES,
    UAE_ZERO_RATED_EXPORTS,
    UAE_DESIGNATED_ZONES,
    UAE_REVERSE_CHARGE_IMPORTS,
]
