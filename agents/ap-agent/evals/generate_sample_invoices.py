"""Regenerate the committed sample-invoice fixtures.

    python agents/ap-agent/evals/generate_sample_invoices.py

Dev tool only — NOT collected by pytest (filename isn't test_*.py) and never
run in CI. Needs Pillow (`pip install -e 'agents/ap-agent[dev]'`); nothing at
runtime or in the test suite imports it. The tests read the committed
`fixtures/invoices/*.png` and `*.expected.json` files this produces.

Everything here is fictional: invented vendors in the made-up jurisdiction
"Larenthia" (borrowed from agents/vat-treatment-agent's synthetic corpus), not
modeled on any real company or invoice. Each image carries a
"SAMPLE — NOT A REAL INVOICE" banner.

The INVOICES list below is the single source of truth: each entry is written
out both as a rendered PNG and as an `.expected.json` file whose shape matches
the `record_invoice` tool input (money and quantities as strings, confidence as
a number), so tests can feed it straight to the fake Anthropic client.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent / "fixtures" / "invoices"

# --- ground truth ----------------------------------------------------------

INVOICES = [
    {
        "slug": "clean_office_supplies",
        "vendor_name": "Nordwind Office Supplies GmbH",
        "vendor_address": ["Hafenstrasse 12", "20099 Musterhafen, Larenthia"],
        "invoice_number": "NB-2026-0417",
        "invoice_date": "2026-07-14",
        "currency": "EUR",
        "line_items": [
            {"description": "Copier paper A4 80gsm (box of 5 reams)", "quantity": "6", "unit_price": "24.50", "line_total": "147.00"},
            {"description": "Toner cartridge, black (XL yield)", "quantity": "3", "unit_price": "89.00", "line_total": "267.00"},
            {"description": "Desk organiser, mesh steel", "quantity": "4", "unit_price": "12.75", "line_total": "51.00"},
        ],
        "grand_total": "465.00",
        "extraction_confidence": 0.97,
        # totals reconcile exactly — the clean happy path.
    },
    {
        "slug": "consulting_services",
        "vendor_name": "Meridian Advisory Partners LLC",
        "vendor_address": ["440 Kingsway, Suite 1200", "Portmarrow, Larenthia"],
        "invoice_number": "MAP-4491",
        "invoice_date": "2026-08-14",
        "currency": "USD",
        "line_items": [
            {"description": "Senior advisory services - August 2026", "quantity": "32", "unit_price": "225.00", "line_total": "7200.00"},
            {"description": "Travel & incidentals (reimbursable, at cost)", "quantity": "1", "unit_price": "512.40", "line_total": "512.40"},
        ],
        "grand_total": "7712.40",
        "extraction_confidence": 0.95,
        # totals reconcile; exercises quantity x unit_price on a services line.
    },
    {
        "slug": "mismatched_totals",
        "vendor_name": "Alpine Logistics Co",
        "vendor_address": ["Zone Industrielle 4", "Col-de-Vaudreuil, Larenthia"],
        "invoice_number": "ALC-99812",
        "invoice_date": "2026-07-30",
        "currency": "USD",
        "line_items": [
            {"description": "Freight - LTL shipment, Depot A to Depot C", "quantity": "1", "unit_price": "1240.00", "line_total": "1240.00"},
            {"description": "Fuel surcharge", "quantity": "1", "unit_price": "186.00", "line_total": "186.00"},
            {"description": "Pallet handling", "quantity": "12", "unit_price": "9.50", "line_total": "114.00"},
            {"description": "After-hours delivery fee", "quantity": "2", "unit_price": "75.00", "line_total": "150.00"},
        ],
        # Each line's quantity x unit_price matches its line_total, but the
        # printed grand total is wrong: 1690.00 lines vs. 1609.00 stated (a
        # digit transposition). The deterministic check must flag +81.00.
        "grand_total": "1609.00",
        "extraction_confidence": 0.90,
    },
]

# --- rendering ------------------------------------------------------------

WIDTH = 900
MARGIN = 48
BG = "white"
INK = (17, 17, 17)
MUTED = (110, 110, 110)
RULE = (200, 200, 200)


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow — unscaled bitmap fallback
        return ImageFont.load_default()


def _expected(invoice: dict) -> dict:
    return {
        "vendor_name": invoice["vendor_name"],
        "invoice_number": invoice["invoice_number"],
        "invoice_date": invoice["invoice_date"],
        "currency": invoice["currency"],
        "line_items": [
            {k: li[k] for k in ("description", "quantity", "unit_price", "line_total")}
            for li in invoice["line_items"]
        ],
        "grand_total": invoice["grand_total"],
        "extraction_confidence": invoice["extraction_confidence"],
    }


def _render(invoice: dict) -> Image.Image:
    rows = invoice["line_items"]
    height = MARGIN * 2 + 300 + len(rows) * 34 + 120
    img = Image.new("RGB", (WIDTH, height), BG)
    d = ImageDraw.Draw(img)

    f_banner = _font(15)
    f_h1 = _font(30)
    f_h2 = _font(18)
    f_body = _font(15)
    f_small = _font(13)

    y = MARGIN
    d.text((MARGIN, y), "SAMPLE - NOT A REAL INVOICE", font=f_banner, fill=MUTED)
    y += 34

    d.text((MARGIN, y), invoice["vendor_name"], font=f_h1, fill=INK)
    y += 40
    for line in invoice["vendor_address"]:
        d.text((MARGIN, y), line, font=f_small, fill=MUTED)
        y += 18
    y += 16

    d.text((MARGIN, y), "INVOICE", font=f_h2, fill=INK)
    d.text((WIDTH - MARGIN - 320, y), f"Invoice no:  {invoice['invoice_number']}", font=f_body, fill=INK)
    y += 24
    d.text((WIDTH - MARGIN - 320, y), f"Date:        {invoice['invoice_date']}", font=f_body, fill=INK)
    y += 24
    d.text((WIDTH - MARGIN - 320, y), f"Currency:    {invoice['currency']}", font=f_body, fill=INK)
    y += 30

    d.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=RULE, width=1)
    y += 12

    col_desc, col_qty, col_price, col_total = MARGIN, WIDTH - MARGIN - 320, WIDTH - MARGIN - 190, WIDTH - MARGIN - 90
    d.text((col_desc, y), "Description", font=f_small, fill=MUTED)
    d.text((col_qty, y), "Qty", font=f_small, fill=MUTED)
    d.text((col_price, y), "Unit price", font=f_small, fill=MUTED)
    d.text((col_total, y), "Amount", font=f_small, fill=MUTED)
    y += 22
    d.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=RULE, width=1)
    y += 10

    for li in rows:
        d.text((col_desc, y), li["description"], font=f_body, fill=INK)
        d.text((col_qty, y), li["quantity"], font=f_body, fill=INK)
        d.text((col_price, y), li["unit_price"], font=f_body, fill=INK)
        d.text((col_total, y), li["line_total"], font=f_body, fill=INK)
        y += 34

    y += 6
    d.line([(col_price, y), (WIDTH - MARGIN, y)], fill=RULE, width=1)
    y += 12
    d.text((col_price, y), "Total due", font=f_h2, fill=INK)
    d.text((col_total, y), f"{invoice['currency']} {invoice['grand_total']}", font=f_h2, fill=INK)
    y += 40

    d.text((MARGIN, y), "Fictional document generated for automated tests. Not a real vendor or invoice.",
           font=f_small, fill=MUTED)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for invoice in INVOICES:
        slug = invoice["slug"]
        _render(invoice).save(OUT_DIR / f"{slug}.png")
        (OUT_DIR / f"{slug}.expected.json").write_text(
            json.dumps(_expected(invoice), indent=2, ensure_ascii=False) + "\n"
        )
        print(f"wrote {slug}.png + {slug}.expected.json")


if __name__ == "__main__":
    main()
