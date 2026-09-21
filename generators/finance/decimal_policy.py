"""Fixed-point decimal policy for Finance generation and validation.

Finance calculations deliberately avoid binary floating-point values. Monetary
amounts use ``DECIMAL(19, 4)``, FX rates use ``DECIMAL(19, 6)``, and every
rounding operation uses ``ROUND_HALF_UP``. FX conversion multiplies the full
fixed-point operands before applying one final monetary quantization.
"""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from decimal import localcontext
from typing import TypeAlias

from generators.finance.config import load_finance_config


DecimalInput: TypeAlias = Decimal | int | str

_POLICY = load_finance_config()["decimal_policy"]
MONEY_PRECISION = int(_POLICY["money_precision"])
MONEY_SCALE = int(_POLICY["money_scale"])
FX_PRECISION = int(_POLICY["fx_precision"])
FX_SCALE = int(_POLICY["fx_scale"])
MONEY_QUANTIZER = Decimal(1).scaleb(-MONEY_SCALE)
FX_QUANTIZER = Decimal(1).scaleb(-FX_SCALE)
MONEY_MULTIPLIER = 10**MONEY_SCALE


def parse_decimal(value: DecimalInput, label: str = "value") -> Decimal:
    """Parse an exact finite decimal value without accepting binary floats."""

    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{label} must be a Decimal, integer, or decimal string")
    if not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"{label} must be a Decimal, integer, or decimal string")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{label} cannot be empty")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} is not a valid decimal value") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def quantize_money(value: DecimalInput) -> Decimal:
    """Round a value to Finance monetary scale using ``ROUND_HALF_UP``."""

    return _quantize(
        value,
        quantizer=MONEY_QUANTIZER,
        precision=MONEY_PRECISION,
        scale=MONEY_SCALE,
        label="money",
    )


def quantize_fx_rate(value: DecimalInput) -> Decimal:
    """Round a value to Finance FX scale using ``ROUND_HALF_UP``."""

    return _quantize(
        value,
        quantizer=FX_QUANTIZER,
        precision=FX_PRECISION,
        scale=FX_SCALE,
        label="FX rate",
    )


def format_money(value: DecimalInput) -> str:
    """Return a monetary value with exactly four fractional digits."""

    return f"{quantize_money(value):.{MONEY_SCALE}f}"


def format_fx_rate(value: DecimalInput) -> str:
    """Return an FX rate with exactly six fractional digits."""

    return f"{quantize_fx_rate(value):.{FX_SCALE}f}"


def money_to_minor_units(value: DecimalInput) -> int:
    """Convert a monetary value to exact integer ten-thousandths."""

    amount = quantize_money(value)
    return int(amount * MONEY_MULTIPLIER)


def minor_units_to_money(value: int) -> Decimal:
    """Convert integer ten-thousandths back to a scale-4 Decimal."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("minor-unit value must be an integer")
    return quantize_money(Decimal(value).scaleb(-MONEY_SCALE))


def allocate_minor_units(total_minor_units: int, part_count: int) -> list[int]:
    """Split signed minor units exactly, placing remainders deterministically.

    The first ``abs(total) % part_count`` allocations receive one additional
    unit. Parts therefore differ by at most one minor unit and always sum back
    to the original total, including for negative values.
    """

    if not isinstance(total_minor_units, int) or isinstance(total_minor_units, bool):
        raise TypeError("total_minor_units must be an integer")
    if not isinstance(part_count, int) or isinstance(part_count, bool):
        raise TypeError("part_count must be an integer")
    if part_count <= 0:
        raise ValueError("part_count must be positive")

    sign = -1 if total_minor_units < 0 else 1
    quotient, remainder = divmod(abs(total_minor_units), part_count)
    return [
        sign * (quotient + (1 if position < remainder else 0))
        for position in range(part_count)
    ]


def allocate_money(value: DecimalInput, part_count: int) -> list[Decimal]:
    """Split a monetary amount into exact scale-4 Decimal allocations."""

    return [
        minor_units_to_money(units)
        for units in allocate_minor_units(money_to_minor_units(value), part_count)
    ]


def convert_to_reporting_currency(
    amount: DecimalInput,
    fx_rate: DecimalInput,
) -> Decimal:
    """Multiply by an FX rate and round once to monetary scale.

    Both inputs are parsed exactly. The FX rate is first normalized to scale 6,
    the amount to scale 4, and their full product is quantized only once at the
    end. Missing rates must be handled by the caller as unconvertible rows.
    """

    source_amount = quantize_money(amount)
    rate = quantize_fx_rate(fx_rate)
    if rate <= 0:
        raise ValueError("FX rate must be positive")
    return quantize_money(source_amount * rate)


def signed_ledger_amount(
    debit_amount: DecimalInput | None,
    credit_amount: DecimalInput | None,
    normal_balance: str,
) -> Decimal:
    """Return one ledger line's signed amount for an account normal balance.

    Exactly one positive debit or credit amount must be supplied. Debit-normal
    accounts treat debits as positive; credit-normal accounts treat credits as
    positive. Reversed transactions require no extra multiplier because their
    debit and credit orientation is swapped when ledger lines are generated.
    """

    debit = _optional_money(debit_amount, "debit_amount")
    credit = _optional_money(credit_amount, "credit_amount")
    if (debit is None) == (credit is None):
        raise ValueError("exactly one debit or credit amount must be populated")
    populated = debit if debit is not None else credit
    if populated is None or populated <= 0:
        raise ValueError("populated ledger amount must be positive")
    if normal_balance == "Debit":
        return populated if debit is not None else -populated
    if normal_balance == "Credit":
        return populated if credit is not None else -populated
    raise ValueError("normal_balance must be 'Debit' or 'Credit'")


def _optional_money(value: DecimalInput | None, label: str) -> Decimal | None:
    """Normalize a nullable ledger-side amount."""

    if value is None or value == "":
        return None
    try:
        return quantize_money(value)
    except (TypeError, ValueError) as exc:
        raise type(exc)(f"{label}: {exc}") from exc


def _quantize(
    value: DecimalInput,
    *,
    quantizer: Decimal,
    precision: int,
    scale: int,
    label: str,
) -> Decimal:
    """Quantize one value and enforce its configured DECIMAL capacity."""

    parsed = parse_decimal(value, label)
    with localcontext() as context:
        context.prec = max(precision + scale + 4, len(parsed.as_tuple().digits) + 8)
        context.rounding = ROUND_HALF_UP
        try:
            rounded = parsed.quantize(quantizer)
        except InvalidOperation as exc:
            raise ValueError(f"{label} cannot be represented at scale {scale}") from exc
    maximum_magnitude = Decimal(10) ** (precision - scale)
    if abs(rounded) >= maximum_magnitude:
        raise ValueError(f"{label} exceeds DECIMAL({precision},{scale}) capacity")
    return rounded


__all__ = [
    "DecimalInput",
    "FX_PRECISION",
    "FX_QUANTIZER",
    "FX_SCALE",
    "MONEY_MULTIPLIER",
    "MONEY_PRECISION",
    "MONEY_QUANTIZER",
    "MONEY_SCALE",
    "allocate_minor_units",
    "allocate_money",
    "convert_to_reporting_currency",
    "format_fx_rate",
    "format_money",
    "minor_units_to_money",
    "money_to_minor_units",
    "parse_decimal",
    "quantize_fx_rate",
    "quantize_money",
    "signed_ledger_amount",
]
