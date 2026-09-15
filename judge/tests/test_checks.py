"""§9.2 T3 NULL-handling and §9.5 rephrase-group checks.

Both are diagnostics reported beside the two scores — they never alter an
`exact_match_result` or a judge dimension (§11.3, §22).
"""

from __future__ import annotations

from judge.checks import CheckStatus, check_null_handling, check_rephrase_groups
from judge.exact_match import ExactMatchOutcome, ExactMatchResult, evaluate_answer

LEFT_JOIN_REF = (
    "SELECT a.account_id, COUNT(c.contact_id) FROM accounts a "
    "LEFT JOIN contacts c ON a.account_id = c.account_id GROUP BY a.account_id"
)


# --- §9.2 T3 ---------------------------------------------------------------


def test_inner_join_answer_to_an_outer_join_question_is_a_finding():
    """The failure T3 exists to catch: unmatched rows silently dropped."""
    check = check_null_handling(
        tier="T3",
        reference_sql=LEFT_JOIN_REF,
        platform_sql=(
            "SELECT a.account_id, COUNT(c.contact_id) FROM accounts a "
            "INNER JOIN contacts c ON a.account_id = c.account_id "
            "GROUP BY a.account_id"
        ),
    )
    assert check.status is CheckStatus.FAIL
    assert check.is_finding
    assert "silently dropped" in check.detail


def test_outer_join_answer_passes():
    check = check_null_handling(
        tier="T3", reference_sql=LEFT_JOIN_REF, platform_sql=LEFT_JOIN_REF
    )
    assert check.status is CheckStatus.PASS


def test_an_equivalent_null_aware_formulation_passes():
    """A platform that reaches the same rows another way is not a defect."""
    for sql in (
        "SELECT a.account_id FROM accounts a WHERE NOT EXISTS "
        "(SELECT 1 FROM contacts c WHERE c.account_id = a.account_id)",
        "SELECT COALESCE(COUNT(c.contact_id), 0) FROM accounts a "
        "JOIN contacts c ON a.account_id = c.account_id",
        "SELECT a.account_id FROM accounts a JOIN contacts c "
        "ON a.account_id = c.account_id WHERE c.contact_id IS NULL",
    ):
        assert (
            check_null_handling(
                tier="T3", reference_sql=LEFT_JOIN_REF, platform_sql=sql
            ).status
            is CheckStatus.PASS
        )


def test_the_check_applies_off_tier_when_the_reference_sql_is_an_outer_join():
    check = check_null_handling(
        tier="T4",
        reference_sql=LEFT_JOIN_REF,
        platform_sql="SELECT 1 FROM a INNER JOIN b ON a.id = b.id",
    )
    assert check.status is CheckStatus.FAIL


def test_not_applicable_off_tier_without_an_outer_join_reference():
    check = check_null_handling(
        tier="T1",
        reference_sql="SELECT COUNT(*) FROM accounts",
        platform_sql="SELECT COUNT(*) FROM accounts",
    )
    assert check.status is CheckStatus.NOT_APPLICABLE


def test_missing_platform_sql_is_unknown_not_pass():
    """§HC-5 makes SQL capture mandatory; absent evidence must not read as a pass."""
    check = check_null_handling(
        tier="T3", reference_sql=LEFT_JOIN_REF, platform_sql=None
    )
    assert check.status is CheckStatus.UNKNOWN
    assert not check.is_finding


# --- §9.5 rephrase groups --------------------------------------------------


def _row(qid, expected, actual, group):
    pair = {"question_id": qid, "rephrase_group_id": group}
    return (pair, expected, evaluate_answer(expected, actual))


def test_agreeing_variants_produce_no_finding():
    findings = check_rephrase_groups(
        [
            _row("q1", "28731", "There are 28,731 cases.", "g1"),
            _row("q2", "28731", "The count is 28731.", "g1"),
            _row("q3", "28731", "28,731 support cases in total.", "g1"),
        ]
    )
    assert len(findings) == 1
    assert not findings[0].is_platform_finding
    assert findings[0].detail == "all variants agree"


def test_disagreeing_variants_are_a_platform_finding():
    """§9.5: 'a group where variants disagree is a platform finding ... it is not
    a defect in our dataset.'"""
    findings = check_rephrase_groups(
        [
            _row("q1", "28731", "There are 28,731 cases.", "g1"),
            _row("q2", "28731", "I could not find an East region.", "g1"),
        ]
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.is_platform_finding
    assert not finding.is_dataset_finding
    assert "q2" in finding.detail


def test_inconsistent_expected_answers_are_blamed_on_the_dataset_not_the_platform():
    """A rephrase group asks ONE underlying question, so differing expected
    answers are our authoring error and must not be reported as a platform bug."""
    findings = check_rephrase_groups(
        [
            _row("q1", "28731", "28,731 cases.", "g1"),
            _row("q2", "28828", "28,828 cases.", "g1"),
        ]
    )
    finding = findings[0]
    assert finding.is_dataset_finding
    assert not finding.is_platform_finding
    assert "one underlying question" in finding.detail


def test_singleton_and_ungrouped_rows_are_skipped():
    assert (
        check_rephrase_groups(
            [
                _row("q1", "28731", "28,731", "g1"),  # lone member
                _row("q2", "28731", "28,731", None),  # not in a group
            ]
        )
        == []
    )


def test_platform_error_variants_are_reported_not_silently_counted():
    rows = [
        _row("q1", "28731", "There are 28,731 cases.", "g1"),
        (
            {"question_id": "q2", "rephrase_group_id": "g1"},
            "28731",
            ExactMatchOutcome(ExactMatchResult.ERROR, "timeout"),
        ),
    ]
    finding = check_rephrase_groups(rows)[0]
    assert not finding.is_platform_finding  # one scorable variant cannot disagree
    assert "not scorable" in finding.detail
