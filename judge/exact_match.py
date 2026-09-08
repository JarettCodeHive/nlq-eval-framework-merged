"""Deterministic exact-match scorer — §HC-3 zero numeric tolerance.

Reported in the scorecard row per §11.2 as `exact_match_result`, alongside the
judge scores. Per §11.3 the two are always shown side by side, NEVER combined
into a composite — they measure different things.

Contract:
- Extract every numeric literal from expected_answer and from actual_answer.
- expected_answer must contain ≥1 numeric to be eligible for exact-match; if
  it doesn't, the pair is judged only (e.g. T5 reasoning wrappers).
- Every numeric in expected_answer must appear in actual_answer (multiset —
  count matters, order does not).
- If any expected numeric is absent → FAIL, no partial credit.

Default is byte-identical (`72` != `72.0`, `1000` != `1,000`). This is
deliberate: OI-2 is the open ticket where the canonical numeric form is agreed
with the Platform Owner. Until then `expected_answer` is the canonical form.

`NumericNormalization` provisionally relaxes that — strip thousands separators,
ignore trailing decimal zeros — for runs that want to score currency/large-value
answers before OI-2 lands. A run using it MUST mark that in its outputs; it is
not the certified comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

# Integers and decimals with optional sign and thousands separators.
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


class ExactMatchResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"  # T5 reasoning wrappers with no numeric core
    ERROR = "error"  # the platform never answered — infra, not accuracy


@dataclass(frozen=True)
class NumericNormalization:
    """Provisional OI-2 relaxation. Off (``None``) is the certified comparison."""

    thousands_separators: bool = True  # 1,000 == 1000
    trailing_decimal_zeros: bool = True  # 72.0 == 72, 72.50 == 72.5

    @property
    def label(self) -> str:
        parts = []
        if self.thousands_separators:
            parts.append("thousands-sep")
        if self.trailing_decimal_zeros:
            parts.append("trailing-zeros")
        return "+".join(parts) or "none"


def extract_numerics(text: str) -> list[str]:
    """Return every numeric literal in `text`, preserving order and formatting."""
    return _NUM.findall(text or "")


def _canonical(token: str, norm: NumericNormalization) -> str:
    raw = token
    if norm.thousands_separators:
        raw = raw.replace(",", "")
    if not norm.trailing_decimal_zeros:
        return raw
    try:
        d = Decimal(raw)
    except InvalidOperation:
        return raw
    if d == d.to_integral_value():
        d = d.to_integral_value()
    else:
        d = d.normalize()
    return format(d, "f")


def canonical_numerics(text: str, norm: NumericNormalization) -> list[str]:
    return [_canonical(t, norm) for t in extract_numerics(text)]


def exact_match(
    expected_answer: str,
    actual_answer: str,
    *,
    normalize: NumericNormalization | None = None,
) -> ExactMatchResult:
    """Numeric multiset comparison. ``NOT_APPLICABLE`` when expected has no
    numeric core; ``PASS`` when every expected numeric is present in actual;
    ``FAIL`` otherwise.

    ``normalize`` is the provisional OI-2 relaxation — leave it ``None`` for the
    certified §HC-3 byte-identical comparison.
    """
    if normalize is None:
        expected_nums = extract_numerics(expected_answer)
        actual_nums = extract_numerics(actual_answer)
    else:
        expected_nums = canonical_numerics(expected_answer, normalize)
        actual_nums = canonical_numerics(actual_answer, normalize)

    if not expected_nums:
        return ExactMatchResult.NOT_APPLICABLE

    actual_multiset = list(actual_nums)  # allow repeats — need same count too
    for n in expected_nums:
        if n not in actual_multiset:
            return ExactMatchResult.FAIL
        actual_multiset.remove(n)
    return ExactMatchResult.PASS
