from __future__ import annotations

from decimal import Decimal

import pytest

from generators.finance.decimal_policy import allocate_minor_units
from generators.finance.decimal_policy import allocate_money
from generators.finance.decimal_policy import convert_to_reporting_currency
from generators.finance.decimal_policy import format_fx_rate
from generators.finance.decimal_policy import format_money
from generators.finance.decimal_policy import FX_PRECISION
from generators.finance.decimal_policy import FX_SCALE
from generators.finance.decimal_policy import minor_units_to_money
from generators.finance.decimal_policy import MONEY_PRECISION
from generators.finance.decimal_policy import MONEY_SCALE
from generators.finance.decimal_policy import money_to_minor_units
from generators.finance.decimal_policy import parse_decimal
from generators.finance.decimal_policy import quantize_fx_rate
from generators.finance.decimal_policy import quantize_money
from generators.finance.decimal_policy import signed_ledger_amount


def test_decimal_policy_uses_configured_precision_and_scale() -> None:
    assert (MONEY_PRECISION, MONEY_SCALE) == (19, 4)
    assert (FX_PRECISION, FX_SCALE) == (19, 6)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.23444", Decimal("1.2344")),
        ("1.23445", Decimal("1.2345")),
        ("-1.23445", Decimal("-1.2345")),
        ("0.00004", Decimal("0.0000")),
        ("0.00005", Decimal("0.0001")),
    ],
)
def test_money_quantization_uses_round_half_up(value: str, expected: Decimal) -> None:
    assert quantize_money(value) == expected
    assert quantize_money(value).as_tuple().exponent == -4


def test_fx_quantization_uses_six_places_and_round_half_up() -> None:
    assert quantize_fx_rate("1.2345674") == Decimal("1.234567")
    assert quantize_fx_rate("1.2345675") == Decimal("1.234568")
    assert quantize_fx_rate("0.0000005") == Decimal("0.000001")


def test_fixed_scale_formatting_preserves_trailing_zeroes() -> None:
    assert format_money("12") == "12.0000"
    assert format_money("12.3") == "12.3000"
    assert format_fx_rate("1") == "1.000000"
    assert format_fx_rate("0.012") == "0.012000"


def test_decimal_parsing_rejects_binary_float_and_non_finite_values() -> None:
    with pytest.raises(TypeError, match="must be a Decimal"):
        parse_decimal(0.1)
    with pytest.raises(ValueError, match="must be finite"):
        parse_decimal("NaN")
    with pytest.raises(ValueError, match="cannot be empty"):
        parse_decimal("")


def test_large_money_values_preserve_precision_and_reject_overflow() -> None:
    maximum = "999999999999999.9999"

    assert format_money(maximum) == maximum
    with pytest.raises(ValueError, match=r"exceeds DECIMAL\(19,4\) capacity"):
        quantize_money("999999999999999.99995")


@pytest.mark.parametrize(
    ("value", "minor_units"),
    [
        ("0.0001", 1),
        ("123.4567", 1234567),
        ("-12.3456", -123456),
    ],
)
def test_money_minor_unit_conversion_is_exact(value: str, minor_units: int) -> None:
    assert money_to_minor_units(value) == minor_units
    assert minor_units_to_money(minor_units) == Decimal(value)
    assert minor_units_to_money(minor_units).as_tuple().exponent == -4


def test_minor_unit_allocation_is_exact_and_deterministic() -> None:
    assert allocate_minor_units(10, 3) == [4, 3, 3]
    assert allocate_minor_units(-10, 3) == [-4, -3, -3]
    assert allocate_minor_units(2, 4) == [1, 1, 0, 0]
    assert sum(allocate_minor_units(100001, 7)) == 100001
    assert (
        max(allocate_minor_units(100001, 7)) - min(allocate_minor_units(100001, 7)) == 1
    )


def test_money_allocation_preserves_scale_and_total() -> None:
    parts = allocate_money("10.0000", 3)

    assert parts == [Decimal("3.3334"), Decimal("3.3333"), Decimal("3.3333")]
    assert sum(parts) == Decimal("10.0000")
    assert all(value.as_tuple().exponent == -4 for value in parts)


@pytest.mark.parametrize("part_count", [0, -1])
def test_allocation_rejects_non_positive_part_count(part_count: int) -> None:
    with pytest.raises(ValueError, match="part_count must be positive"):
        allocate_minor_units(10, part_count)


def test_fx_conversion_multiplies_then_rounds_once() -> None:
    assert convert_to_reporting_currency("100.0050", "1.234567") == Decimal("123.4629")
    assert convert_to_reporting_currency("0.0001", "0.500000") == Decimal("0.0001")
    assert convert_to_reporting_currency("125.0000", "1.000000") == Decimal("125.0000")


def test_fx_conversion_rejects_non_positive_rates() -> None:
    with pytest.raises(ValueError, match="FX rate must be positive"):
        convert_to_reporting_currency("10.0000", "0.000000")


@pytest.mark.parametrize(
    ("debit", "credit", "normal_balance", "expected"),
    [
        ("10.0000", None, "Debit", Decimal("10.0000")),
        (None, "10.0000", "Debit", Decimal("-10.0000")),
        (None, "10.0000", "Credit", Decimal("10.0000")),
        ("10.0000", None, "Credit", Decimal("-10.0000")),
    ],
)
def test_signed_ledger_amount_respects_normal_balance(
    debit: str | None,
    credit: str | None,
    normal_balance: str,
    expected: Decimal,
) -> None:
    assert signed_ledger_amount(debit, credit, normal_balance) == expected


@pytest.mark.parametrize(
    ("debit", "credit", "message"),
    [
        (None, None, "exactly one"),
        ("1.0000", "1.0000", "exactly one"),
        ("0.0000", None, "must be positive"),
        (None, "-1.0000", "must be positive"),
    ],
)
def test_signed_ledger_amount_rejects_invalid_sides(
    debit: str | None, credit: str | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        signed_ledger_amount(debit, credit, "Debit")


def test_signed_ledger_amount_rejects_unknown_normal_balance() -> None:
    with pytest.raises(ValueError, match="normal_balance"):
        signed_ledger_amount("1.0000", None, "Unknown")
