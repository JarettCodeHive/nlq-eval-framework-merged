"""
Canonical serialization of a DuckDB result into an `expected_answer`
string. Under zero numeric tolerance (scope doc Section 9.4) the string
form must be deterministic and identical within a measure type, so this
is the ONLY place a query result becomes an answer string.

Rules (OI-2 interim default, Section 8.1 CSV convention):
  * period decimal separator, no thousands separator
  * money / budget           -> 2 decimal places, always (Decimal, not float)
  * counts                   -> plain integer
  * percentages / ratios     -> 2 decimal places
  * dates                    -> ISO 8601 date (YYYY-MM-DD)
  * timestamps                -> ISO 8601, keeping any non-zero time
                                (YYYY-MM-DD when exactly midnight,
                                 else YYYY-MM-DDTHH:MM:SS)
  * booleans                 -> lowercase true / false
Row / column joins:
  * one row, N columns   -> "a | b | c"
  * M rows               -> "r1a | r1b; r2a | r2b"
"""

from __future__ import annotations

import datetime
import decimal

MONEY_COLUMN_HINTS = ("budget", "amount", "value", "revenue", "spend", "cost")
PERCENT_COLUMN_HINTS = ("pct", "percent", "rate", "ratio", "change")

_TWOPLACES = decimal.Decimal("0.01")


class SerializationError(ValueError):
    """Raised when a result cannot be turned into a valid answer string."""


def _column_kind(name: str) -> str:
    low = (name or "").lower()
    if any(h in low for h in PERCENT_COLUMN_HINTS):
        return "percent"
    if any(h in low for h in MONEY_COLUMN_HINTS):
        return "money"
    return "auto"


def _cell(value, kind: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.replace(microsecond=0).isoformat(sep="T")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        if kind in ("money", "percent"):
            return str(value.quantize(_TWOPLACES, rounding=decimal.ROUND_HALF_UP))
        # a Decimal with a fractional part keeps it; whole number -> int
        return str(value if value % 1 else value.to_integral_value())
    if isinstance(value, float):
        if kind in ("money", "percent"):
            return str(
                decimal.Decimal(repr(value)).quantize(_TWOPLACES, rounding=decimal.ROUND_HALF_UP)
            )
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def serialize(rows: list[tuple], columns: list[str]) -> str:
    """Turn a fetched result set into the canonical answer string.

    Raises SerializationError on an empty result or a 1x1 NULL where a
    number is expected — callers treat that as a blocked pair.
    """
    if not rows:
        raise SerializationError("zero rows")

    kinds = [_column_kind(c) for c in columns]

    if len(rows) == 1 and len(columns) == 1:
        value = rows[0][0]
        if value is None:
            raise SerializationError("NULL scalar where a value is expected")
        return _cell(value, kinds[0])

    def fmt_row(row: tuple) -> str:
        return " | ".join(_cell(v, k) for v, k in zip(row, kinds))

    if len(rows) == 1:
        return fmt_row(rows[0])
    return "; ".join(fmt_row(r) for r in rows)
