"""Parse and validate the judge's JSON output.

Reject invalid output before reporting. A score of 7, a score of "high", or a
missing dimension must raise — never be clamped, coerced, or defaulted. A
clamped score is a fabricated measurement.
"""

from __future__ import annotations

import json

from judge.contracts import DIMENSIONS


class JudgeOutputError(ValueError):
    """The judge returned something we refuse to score with."""


def extract_json(raw: str) -> dict:
    """Get a JSON object out of a model response.

    Strict JSON only. Prose wrappers and code fences are malformed output and
    must be retried by the caller rather than silently repaired here.
    """
    text = (raw or "").strip()
    if not text:
        raise JudgeOutputError("judge returned an empty response")

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeOutputError(f"malformed JSON in judge response: {exc}") from exc

    if not isinstance(obj, dict):
        raise JudgeOutputError(f"expected a JSON object, got {type(obj).__name__}")
    return obj


def _validate_score(value: object, field: str) -> int:
    """Validate one 1-5 score without coercion."""
    if type(value) is not int:  # bool, 5.0 and "5" must all be rejected
        raise JudgeOutputError(f"{field}: expected an integer 1-5, got {value!r}")
    if not 1 <= value <= 5:
        raise JudgeOutputError(f"{field}: score {value} is outside the valid range 1-5")
    return value


def parse_combined(raw: str) -> dict:
    """Parse a combined-mode response: rationale + all four dimensions."""
    obj = extract_json(raw)

    expected_keys = set(DIMENSIONS) | {"rationales"}
    if set(obj) != expected_keys:
        raise JudgeOutputError(
            "judge response keys must match the structured-output schema; "
            f"missing={sorted(expected_keys - set(obj))}, "
            f"extra={sorted(set(obj) - expected_keys)}"
        )

    missing = [d for d in DIMENSIONS if d not in obj]
    if missing:
        raise JudgeOutputError(
            f"judge response missing dimension(s): {', '.join(missing)}"
        )

    rationales = obj.get("rationales")
    if not isinstance(rationales, dict):
        raise JudgeOutputError("judge response missing a 'rationales' object")
    missing_rationales = [d for d in DIMENSIONS if d not in rationales]
    extra_rationales = [key for key in rationales if key not in DIMENSIONS]
    if missing_rationales or extra_rationales:
        raise JudgeOutputError(
            "judge rationales must match the four dimensions; "
            f"missing={missing_rationales}, extra={extra_rationales}"
        )
    for dimension, rationale in rationales.items():
        if not isinstance(rationale, str) or not rationale.strip():
            raise JudgeOutputError(
                f"judge response missing a non-empty rationale for {dimension!r}"
            )

    return {
        "dimension_rationales": {
            dimension: rationales[dimension].strip() for dimension in DIMENSIONS
        },
        **{d: _validate_score(obj[d], d) for d in DIMENSIONS},
    }


def parse_dimension(raw: str, dimension: str) -> tuple[int, str]:
    """Parse a per-dimension response. Returns (score, rationale)."""
    obj = extract_json(raw)
    expected_keys = {dimension, "rationale"}
    if set(obj) != expected_keys:
        raise JudgeOutputError(
            f"dimension response keys must be {sorted(expected_keys)}; "
            f"missing={sorted(expected_keys - set(obj))}, "
            f"extra={sorted(set(obj) - expected_keys)}"
        )
    if dimension not in obj:
        raise JudgeOutputError(f"judge response missing dimension {dimension!r}")
    rationale = obj.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise JudgeOutputError("judge response missing a non-empty 'rationale'")
    return _validate_score(obj[dimension], dimension), rationale.strip()
