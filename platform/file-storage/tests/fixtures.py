"""Fictional tenants and tiny fake files for the file-storage prototype.

Nothing here is real — a 1x1 PNG and a stub text document. Do not reuse.
"""

import base64

FICTIONAL_TENANTS = [
    ("acme-books", "Acme Bookkeeping LLC"),
    ("globex-finance", "Globex Finance Co"),
]

# The canonical 1x1 transparent PNG (68 bytes).
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SAMPLE_DOC = (
    b"SAMPLE RECEIPT - NOT A REAL DOCUMENT\n"
    b"Vendor: Fictional Cafe\nAmount: 12.00 USD\nDate: 2026-09-01\n"
)
