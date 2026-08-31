from decimal import Decimal

import pytest

from ap_agent.money import parse_decimal


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("147.00", Decimal("147.00")),
        ("  147.00 ", Decimal("147.00")),
        ("$1,240.00", Decimal("1240.00")),
        ("EUR 465.00", Decimal("465.00")),
        ("-42.50", Decimal("-42.50")),
        ("7200", Decimal("7200")),
        (512.40, Decimal("512.40")),
        (6, Decimal("6")),
        (Decimal("9.50"), Decimal("9.50")),
    ],
)
def test_parses_common_shapes(raw, expected):
    assert parse_decimal(raw) == expected


def test_float_uses_repr_not_binary_expansion():
    assert parse_decimal(9.50) == Decimal("9.5")
    assert str(parse_decimal(0.1)) == "0.1"


@pytest.mark.parametrize("raw", ["", "  ", "n/a", "--", True, None, ["x"]])
def test_rejects_unparseable(raw):
    with pytest.raises((ValueError, TypeError)):
        parse_decimal(raw)
