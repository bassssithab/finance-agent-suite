"""Synthetic fixtures for the ap-agent eval suite.

Nothing here is real: "Larenthia Trading Co" and its chart of accounts are
invented for this test corpus (same fictional jurisdiction as
agents/vat-treatment-agent's synthetic VAT code), not modeled on any real
company or GL structure. It exists to exercise retrieval, citation, and the
"account not in the chart -> null, don't guess" path.

`load_expected(slug)` reads the committed ground truth that
generate_sample_invoices.py wrote next to each PNG; `record_invoice_payload`
returns it in the exact shape the model's forced tool call would produce, so a
test can hand it straight to fakes.invoice_client(...).
"""

import json
from pathlib import Path

from knowledge import Document

INVOICE_DIR = Path(__file__).parent / "fixtures" / "invoices"


def load_expected(slug: str) -> dict:
    return json.loads((INVOICE_DIR / f"{slug}.expected.json").read_text())


# The model's record_invoice tool input == the expected.json shape as authored.
record_invoice_payload = load_expected


# --- synthetic chart of accounts -----------------------------------------

CHART_OF_ACCOUNTS = Document(
    doc_id="doc-coa-larenthia-trading",
    title="Larenthia Trading Co - Chart of Accounts (Synthetic Fixture)",
    corpus="chart_of_accounts",
    metadata={"framework": "synthetic", "entity": "larenthia-trading", "period": "FY2026"},
    # Kept under platform/knowledge's 800-char chunk size so the whole chart
    # lands in a single chunk (position 0) — see the COA_CITATION check in
    # test_coding.py.
    text="""\
Placeholder text for testing GL coding, not a real chart of accounts.

6100 Office Supplies & Stationery: consumable office supplies - copier and \
printer paper, toner and ink cartridges, pens, notebooks, desk accessories and \
organisers. Excludes IT hardware and furniture.

6200 Professional & Advisory Fees: fees from external professional firms - \
management consulting, legal, audit, and tax advisory. Reimbursable travel \
billed by an advisor is coded here with the related engagement, not to the \
company's own travel account.

6300 Freight, Carriage & Distribution: third-party carrier charges - line-haul \
and LTL shipping, fuel surcharges, pallet and handling fees, and delivery \
premiums such as after-hours or expedited delivery.
""",
)

ALL_DOCUMENTS = [CHART_OF_ACCOUNTS]

# Citation label the KnowledgeBase produces for the (single) chunk of the doc
# above. chunk_document packs the whole doc into one chunk (position 0).
COA_CITATION = (
    "Larenthia Trading Co - Chart of Accounts (Synthetic Fixture) "
    "(chart_of_accounts), chunk 0"
)


# --- canned record_gl_coding payloads (what the model would return) ------

CODING_PAYLOADS = {
    "clean_office_supplies": {
        "suggestions": [
            {"line_index": 0, "account_code": "6100", "account_name": "Office Supplies & Stationery",
             "rationale": "Copier paper is a consumable office supply.", "citation": COA_CITATION},
            {"line_index": 1, "account_code": "6100", "account_name": "Office Supplies & Stationery",
             "rationale": "Toner cartridges are listed under 6100.", "citation": COA_CITATION},
            {"line_index": 2, "account_code": "6100", "account_name": "Office Supplies & Stationery",
             "rationale": "Desk organisers are named as desk accessories under 6100.", "citation": COA_CITATION},
        ]
    },
    "consulting_services": {
        "suggestions": [
            {"line_index": 0, "account_code": "6200", "account_name": "Professional & Advisory Fees",
             "rationale": "Senior advisory services are management consulting fees.", "citation": COA_CITATION},
            {"line_index": 1, "account_code": "6200", "account_name": "Professional & Advisory Fees",
             "rationale": "Reimbursable advisor travel is coded with the related engagement, per 6200.",
             "citation": COA_CITATION},
        ]
    },
    "mismatched_totals": {
        "suggestions": [
            {"line_index": 0, "account_code": "6300", "account_name": "Freight, Carriage & Distribution",
             "rationale": "LTL line-haul shipping falls under 6300.", "citation": COA_CITATION},
            {"line_index": 1, "account_code": "6300", "account_name": "Freight, Carriage & Distribution",
             "rationale": "Fuel surcharges are listed under 6300.", "citation": COA_CITATION},
            {"line_index": 2, "account_code": "6300", "account_name": "Freight, Carriage & Distribution",
             "rationale": "Pallet and handling fees are listed under 6300.", "citation": COA_CITATION},
            {"line_index": 3, "account_code": "6300", "account_name": "Freight, Carriage & Distribution",
             "rationale": "After-hours delivery is named as a delivery premium under 6300.", "citation": COA_CITATION},
        ]
    },
}
