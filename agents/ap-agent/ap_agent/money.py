"""Deterministic parsing of money/quantity values out of model output.

CLAUDE.md rule #4: the model transcribes what it sees on the invoice (a string
or a JSON number); turning that into an exact `Decimal` for the arithmetic
check is plain code, with its own tests. Nothing here rounds or computes — it
only normalizes a single scalar.
"""

import re
from decimal import Decimal, InvalidOperation

_STRIP_RE = re.compile(r"[^0-9.\-]")


def parse_decimal(raw) -> Decimal:
    """Normalize a model-supplied number to Decimal.

    Accepts a Decimal/int (returned as-is / exact), a float (via its repr, so
    ``12.4`` not ``12.399999...``), or a string that may carry a currency
    symbol, thousands separators, or surrounding whitespace
    (``"$1,240.00"`` -> ``Decimal("1240.00")``).
    """
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, bool):  # bool is an int subclass — reject explicitly
        raise TypeError(f"cannot parse a boolean as a number: {raw!r}")
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(str(raw))
    if isinstance(raw, str):
        cleaned = _STRIP_RE.sub("", raw.strip())
        if cleaned in ("", "-", ".", "-.", "."):
            raise ValueError(f"cannot parse a number from {raw!r}")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"cannot parse a number from {raw!r}") from exc
    raise TypeError(f"unsupported number value of type {type(raw).__name__}: {raw!r}")
