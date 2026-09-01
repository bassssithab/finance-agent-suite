from decimal import Decimal

import pytest

from expense_agent.money import parse_decimal


def test_passes_decimal_through_unchanged():
    d = Decimal("12.34")
    assert parse_decimal(d) is d


def test_int_and_float():
    assert parse_decimal(5) == Decimal("5")
    assert parse_decimal(12.4) == Decimal("12.4")  # via str(), not binary float


def test_strips_currency_symbol_and_thousands_separator():
    assert parse_decimal("$1,240.00") == Decimal("1240.00")
    assert parse_decimal("  182.50 ") == Decimal("182.50")


def test_rejects_boolean():
    with pytest.raises(TypeError):
        parse_decimal(True)


def test_rejects_unparseable_string():
    for bad in ("", "  ", "n/a", "-", "."):
        with pytest.raises(ValueError):
            parse_decimal(bad)


def test_rejects_unsupported_type():
    with pytest.raises(TypeError):
        parse_decimal(["1.00"])
