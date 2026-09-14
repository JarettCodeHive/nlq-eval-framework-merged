"""Exercise the three scoring modes (utils/scoring.py) against the real
released answer key. This is the test the review asked for: it runs the
evaluator and proves that a numeric change fails at zero tolerance -
rather than just asserting a routing split.
"""

from decimal import Decimal

import pytest

from utils.scoring import (
    MODES,
    numbers,
    score,
    score_judge_plus_exact,
    score_scalar_exact,
    score_table_exact,
)

ZERO = Decimal("0")


def _mode_for(companion, q):
    for c in companion:
        if c["natural_language_question"] == q:
            return c["scoring_mode"]
    raise KeyError(q)


# ---- identity: the golden answer always scores itself as a pass ----------


def test_every_released_answer_passes_against_itself(pairs, companion):
    for r in pairs:
        mode = _mode_for(companion, r["natural_language_question"])
        v = score(mode, r["expected_answer"], r["expected_answer"], judge_verdict=True)
        assert v.passed, (r["natural_language_question"], mode, v.reason)


# ---- zero tolerance: bump the first number and it must fail --------------


def _bump_first_number(answer: str) -> str:
    nums = numbers(answer)
    if not nums:
        return answer
    first = nums[0]
    target = str(first)
    replacement = str(
        first + (Decimal("1") if first == first.to_integral_value() else Decimal("0.01"))
    )
    return answer.replace(target, replacement, 1)


def test_numeric_change_fails_for_every_numeric_answer(pairs, companion):
    checked = 0
    for r in pairs:
        golden = r["expected_answer"]
        if not numbers(golden):
            continue
        mode = _mode_for(companion, r["natural_language_question"])
        tampered = _bump_first_number(golden)
        if tampered == golden:
            continue
        v = score(mode, golden, tampered, judge_verdict=True)
        assert not v.passed, (r["natural_language_question"], mode, golden, "->", tampered)
        checked += 1
    assert checked > 100  # the vast majority of the 160 carry a number


# ---- scalar_exact: first-number extraction from a wrapped response ------


def test_scalar_exact_extracts_first_number_from_prose():
    v = score_scalar_exact("3663", "The platform found 3663 active devices.")
    assert v.passed
    v = score_scalar_exact("3663", "There were 3,663 devices (up from 3100).")
    assert v.passed  # thousands separator tolerated on input
    v = score_scalar_exact("1772515.87", "Total budget is $1,772,515.87.")
    assert v.passed
    v = score_scalar_exact("3663", "About 3664 devices.")
    assert not v.passed


def test_scalar_exact_non_numeric_falls_back_to_string_match():
    assert score_scalar_exact("Central", "Central").passed
    assert not score_scalar_exact("Central", "Northeast").passed


# ---- table_exact: order and every cell matter ---------------------------


def test_table_exact_is_row_and_cell_sensitive():
    golden = "Email | 900; Phone | 400"
    assert score_table_exact(golden, "Email | 900; Phone | 400").passed
    assert not score_table_exact(golden, "Phone | 400; Email | 900").passed  # order
    assert not score_table_exact(golden, "Email | 901; Phone | 400").passed  # cell
    assert not score_table_exact(golden, "Email | 900").passed  # row count


# ---- judge_plus_exact: numbers gate the pass, judge gates the prose -----


def test_judge_plus_exact_requires_every_numeric_component():
    golden = "West | 12000 | 15000 | 3000 | 25.00 | increase"
    ok_resp = "Engagement in the West rose from 12000 to 15000, up 3000 (25.00%)."
    assert score_judge_plus_exact(golden, ok_resp, judge_verdict=True).passed
    assert score_judge_plus_exact(golden, ok_resp, judge_verdict=None).passed
    # judge rejects the reasoning even though the numbers line up
    assert not score_judge_plus_exact(golden, ok_resp, judge_verdict=False).passed
    # one number wrong -> fail regardless of the judge
    bad = "West rose from 12000 to 15000, up 3000 (26.00%)."
    assert not score_judge_plus_exact(golden, bad, judge_verdict=True).passed


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        score("first_number_wins", "1", "1")
    assert set(MODES) == {"scalar_exact", "table_exact", "judge_plus_exact"}
