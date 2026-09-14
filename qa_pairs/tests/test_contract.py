"""Delivery-contract gates (scope doc Section 9.1 / 9.3 / 9.4)."""

import re

CONTRACT_FIELDS = [
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
]
TIER_QUOTA = {"T1": 32, "T2": 40, "T3": 32, "T4": 32, "T5": 24}
_NORM = re.compile(r"[^a-z0-9]+")


def _norm(q):
    return _NORM.sub(" ", q.lower()).strip()


def test_contract_has_exactly_seven_named_fields(pairs):
    assert list(pairs[0].keys()) == CONTRACT_FIELDS


def test_no_internal_metadata_in_contract(pairs):
    for r in pairs:
        assert "question_id" not in r and "tier" not in r


def test_total_is_exactly_160(pairs):
    assert len(pairs) == 160


def test_tier_counts_match_quota(companion):
    counts = {}
    for r in companion:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    assert counts == TIER_QUOTA


def test_companion_aligns_with_contract(pairs, companion):
    assert len(companion) == len(pairs)
    ids = [r["question_id"] for r in companion]
    assert len(ids) == len(set(ids)), "duplicate question_id"
    assert {_norm(r["natural_language_question"]) for r in pairs} == {
        _norm(r["natural_language_question"]) for r in companion
    }


def test_questions_are_unique(pairs):
    seen = set()
    for r in pairs:
        k = _norm(r["natural_language_question"])
        assert k not in seen, f"duplicate question: {r['natural_language_question']}"
        seen.add(k)


def test_no_raw_enum_or_column_tokens_in_questions(pairs):
    banned = [
        "FormSubmission",
        "GeneralInquiry",
        "EmailOpen",
        "LinkClick",
        "PhoneCall",
        "WebinarAttendance",
        "EventAttendance",
        "CustomerEducation",
        "ProductIssue",
        "ServiceRequest",
        "PendingCustomer",
        "InProgress",
        "FinancialServices",
        "engagement_points",
        "engagement-point",
        "account_id",
        "contact_id",
        "campaign_id",
    ]
    for r in pairs:
        q = r["natural_language_question"]
        hit = [t for t in banned if t in q]
        assert not hit, f"{hit} in question: {q}"


def test_reference_sql_ends_with_semicolon(pairs):
    for r in pairs:
        assert r["reference_sql"].rstrip().endswith(";")


def test_expected_answer_is_never_empty(pairs):
    for r in pairs:
        assert r["expected_answer"].strip(), r["natural_language_question"]


_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
_MODES = {"scalar_exact", "table_exact", "judge_plus_exact"}
_TIER_MODE = {  # scope Section 9.2
    "T1": {"scalar_exact"},
    "T2": {"scalar_exact", "table_exact"},
    "T3": {"scalar_exact"},
    "T4": {"table_exact"},
    "T5": {"judge_plus_exact"},
}


def test_every_pair_has_a_valid_scoring_mode(companion):
    """Section 9.4 scoring path: every pair declares one of the three modes,
    and the mode is legal for its tier (Section 9.2)."""
    for r in companion:
        assert r["scoring_mode"] in _MODES, (r["question_id"], r["scoring_mode"])
        assert r["scoring_mode"] in _TIER_MODE[r["tier"]], (
            r["question_id"],
            r["tier"],
            r["scoring_mode"],
        )


def test_scalar_exact_only_on_single_plain_number_answers(pairs, companion):
    """`scalar_exact` means the deterministic first-number evaluator can
    verify it: one row, one plain number. Anything else must be table_exact
    or judge_plus_exact."""
    by_q = {r["natural_language_question"]: r for r in pairs}
    bad = []
    for c in companion:
        if c["scoring_mode"] != "scalar_exact":
            continue
        a = by_q[c["natural_language_question"]]["expected_answer"].strip()
        if "|" in a or ";" in a or not _NUMERIC.match(a):
            bad.append((c["question_id"], a))
    assert not bad, bad


def test_judge_versions_present_only_for_judge_mode(companion):
    for c in companion:
        judged = c["scoring_mode"] == "judge_plus_exact"
        assert bool(c["judge_rubric_version"]) is judged, c["question_id"]
        assert bool(c["judge_prompt_version"]) is judged, c["question_id"]
        assert c["scorer_status"] in ("draft", "approved", "calibrated")


def test_numeric_components_declared_for_every_numeric_answer(pairs, companion):
    """Every numeric cell in a golden answer must be named in
    `numeric_components` so the scorer knows what compares at zero tolerance."""
    from utils.scoring import numbers

    by_q = {r["natural_language_question"]: r for r in pairs}
    for c in companion:
        a = by_q[c["natural_language_question"]]["expected_answer"]
        if numbers(a) and c["scoring_mode"] != "judge_plus_exact":
            assert c["numeric_components"] not in ("", "(none)"), c["question_id"]


def test_scoring_mode_split_matches_expected_counts(companion):
    n = {m: 0 for m in _MODES}
    for r in companion:
        n[r["scoring_mode"]] += 1
    assert n == {"scalar_exact": 93, "table_exact": 43, "judge_plus_exact": 24}


def test_scalar_answers_carry_no_currency_or_thousands_separator(pairs):
    """OI-2: golden answers are plain numbers - the deterministic evaluator
    extracts and compares numerically, so no $ / , / unit noun."""
    for r in pairs:
        a = r["expected_answer"]
        if "|" in a or ";" in a:  # multi-value -> judge-scored, skip
            continue
        assert "$" not in a and "," not in a and "%" not in a, (a, r["natural_language_question"])
