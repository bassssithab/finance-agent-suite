"""Regenerate the committed sample-receipt fixtures.

    python agents/expense-agent/evals/generate_sample_receipts.py

Dev tool only — NOT collected by pytest (filename isn't test_*.py) and never run
in CI. Needs Pillow (`pip install -e 'agents/expense-agent[dev]'`); nothing at
runtime or in the test suite imports it. The tests read the committed
`fixtures/receipts/*.png` and `*.expected.json` files this produces.

Everything here is fictional: invented merchants in the made-up jurisdiction
"Larenthia" (borrowed from agents/vat-treatment-agent's synthetic corpus), not
modeled on any real merchant or receipt. Each image carries a
"SAMPLE — NOT A REAL RECEIPT" banner.

The RECEIPTS list below is the single source of truth: each entry is written out
both as a rendered PNG and as an `.expected.json` file whose shape matches the
`record_receipt` tool input (amount as a string, confidence as a number,
expense_category as the label a model should infer from the merchant + items),
so tests can feed it straight to the fake Anthropic client.

Dates are chosen relative to evals/fixtures.py AS_OF (2026-09-01): the taxi is a
few days old, the hotel is ~5 months old (over a 90-day policy age limit).
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent / "fixtures" / "receipts"

# --- ground truth ----------------------------------------------------------

RECEIPTS = [
    {
        "slug": "compliant_taxi",
        "vendor": "Larkspur City Cabs",
        "vendor_address": ["Rank 4, Union Station", "Larkspur, Larenthia"],
        "date": "2026-08-28",
        "lines": [
            {"label": "Fare (Airport -> Downtown)", "amount": "31.00"},
            {"label": "Booking fee", "amount": "2.40"},
            {"label": "Tip", "amount": "5.00"},
        ],
        "amount": "38.40",
        "currency": "USD",
        "expense_category": "Travel - taxi",
        "extraction_confidence": 0.96,
        # within a per-category taxi limit, only a few days old -> compliant.
    },
    {
        "slug": "over_limit_dinner",
        "vendor": "The Copper Table",
        "vendor_address": ["18 Harbour Promenade", "Portmarrow, Larenthia"],
        "date": "2026-08-25",
        "lines": [
            {"label": "Tasting menu x2", "amount": "150.00"},
            {"label": "Wine pairing", "amount": "24.00"},
            {"label": "Service 12%", "amount": "8.50"},
        ],
        "amount": "182.50",
        "currency": "USD",
        "expense_category": "Meals",
        "extraction_confidence": 0.94,
        # recent, all fields present, but well over a per-meal limit.
    },
    {
        "slug": "stale_hotel",
        "vendor": "Harbour View Inn",
        "vendor_address": ["2 Lighthouse Road", "Col-de-Vaudreuil, Larenthia"],
        "date": "2026-04-03",
        "lines": [
            {"label": "Room, 1 night", "amount": "190.00"},
            {"label": "City tax", "amount": "12.00"},
            {"label": "Breakfast", "amount": "10.00"},
        ],
        "amount": "212.00",
        "currency": "USD",
        "expense_category": "Lodging",
        "extraction_confidence": 0.95,
        # within a per-night lodging limit and complete, but ~5 months old.
    },
]

# --- rendering ------------------------------------------------------------

WIDTH = 460
MARGIN = 32
BG = "white"
INK = (17, 17, 17)
MUTED = (110, 110, 110)
RULE = (200, 200, 200)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow — unscaled bitmap fallback
        return ImageFont.load_default()


def _expected(receipt: dict) -> dict:
    return {
        "vendor": receipt["vendor"],
        "date": receipt["date"],
        "amount": receipt["amount"],
        "currency": receipt["currency"],
        "expense_category": receipt["expense_category"],
        "extraction_confidence": receipt["extraction_confidence"],
    }


def _render(receipt: dict) -> Image.Image:
    rows = receipt["lines"]
    height = MARGIN * 2 + 210 + len(rows) * 26 + 90
    img = Image.new("RGB", (WIDTH, height), BG)
    d = ImageDraw.Draw(img)

    f_banner = _font(13)
    f_h1 = _font(22)
    f_body = _font(14)
    f_small = _font(12)

    y = MARGIN
    d.text((MARGIN, y), "SAMPLE - NOT A REAL RECEIPT", font=f_banner, fill=MUTED)
    y += 28

    d.text((MARGIN, y), receipt["vendor"], font=f_h1, fill=INK)
    y += 30
    for line in receipt["vendor_address"]:
        d.text((MARGIN, y), line, font=f_small, fill=MUTED)
        y += 16
    y += 10

    d.text((MARGIN, y), f"Date: {receipt['date']}", font=f_body, fill=INK)
    y += 24
    d.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=RULE, width=1)
    y += 12

    amount_x = WIDTH - MARGIN - 90
    for li in rows:
        d.text((MARGIN, y), li["label"], font=f_body, fill=INK)
        d.text((amount_x, y), li["amount"], font=f_body, fill=INK)
        y += 26

    y += 6
    d.line([(amount_x, y), (WIDTH - MARGIN, y)], fill=RULE, width=1)
    y += 10
    d.text((MARGIN, y), "TOTAL", font=f_body, fill=INK)
    d.text((amount_x, y), f"{receipt['currency']} {receipt['amount']}", font=f_body, fill=INK)
    y += 34

    d.text((MARGIN, y), "Fictional document generated for automated tests.", font=f_small, fill=MUTED)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for receipt in RECEIPTS:
        slug = receipt["slug"]
        _render(receipt).save(OUT_DIR / f"{slug}.png")
        (OUT_DIR / f"{slug}.expected.json").write_text(
            json.dumps(_expected(receipt), indent=2, ensure_ascii=False) + "\n"
        )
        print(f"wrote {slug}.png + {slug}.expected.json")


if __name__ == "__main__":
    main()
