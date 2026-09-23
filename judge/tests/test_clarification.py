"""A clarification request is its own outcome, scored at a fixed 2.5 (§2e).

The platform sometimes answers with a question instead of an answer
(`clarify: true`). That is neither a wrong answer nor an outage, and the
catalogue in `docs/findings/crm_failure_catalogue_20260921T124500Z.md` §2e found
11 of them in 160 pairs.
"""

from __future__ import annotations

import asyncio
import json

from judge.cli import PlatformError, _collect_and_score, _summarise
from judge.contracts import (
    CLARIFICATION_SCORE,
    ClarificationVerdict,
    JudgeRequest,
    JudgeVerdict,
    PulseResponse,
)
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from judge.heuristic_judge import HeuristicJudge
from judge.pulse_client import _extract_clarify
from scorecard.summary import RunContext, aggregate, build_summary_rows


def _pairs(n: int) -> list[dict]:
    return [
        {
            "question_id": f"q{i}",
            "domain": "crm",
            "tier": "T1",
            "natural_language_question": f"question {i}?",
            "expected_answer": "72",
            "judge_reference": "There are 72.",
            "reference_sql": "SELECT 72",
        }
        for i in range(n)
    ]


class _ClarifyingPulse:
    """Declines the ids in `clarify`, answers everyone else."""

    mode = "live"

    def __init__(self, clarify: set[str]):
        self.clarify = clarify

    def query(self, question_id: str) -> PulseResponse:
        if question_id in self.clarify:
            return PulseResponse(
                question_id=question_id,
                answer_text="Which campaign type did you mean?",
                generated_sql=None,
                clarify=True,
            )
        return PulseResponse(
            question_id=question_id, answer_text="72 things", generated_sql="SELECT 72"
        )


class _CountingJudge(HeuristicJudge):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def judge(self, req):
        self.calls += 1
        return await super().judge(req)


# --- detection ---------------------------------------------------------------


def test_clarify_is_found_inside_the_stringified_response() -> None:
    """The flag is nested in a JSON *string*, which is how it was first missed."""

    payload = {
        "response": json.dumps({"analysis": "Which one?", "clarify": True}),
        "analysis_request": [],
    }
    assert _extract_clarify(payload) is True


def test_clarify_is_found_on_the_envelope_too() -> None:
    assert _extract_clarify({"clarify": True, "response": "{}"}) is True


def test_a_normal_answer_is_not_a_clarification() -> None:
    payload = {"response": json.dumps({"analysis": "There are 72.", "clarify": False})}
    assert _extract_clarify(payload) is False
    assert _extract_clarify({"response": "not json at all"}) is False
    assert _extract_clarify({}) is False


# --- the runner --------------------------------------------------------------


def test_a_clarification_is_never_sent_to_the_judge() -> None:
    """The score is fixed, so a provider call would cost ~12s and change nothing."""

    judge = _CountingJudge()
    collected = asyncio.run(
        _collect_and_score(
            _pairs(4),
            _ClarifyingPulse(clarify={"q1", "q3"}),
            judge,
            pulse_concurrency=4,
            judge_concurrency=4,
        )
    )

    by_id = {pair["question_id"]: verdict for pair, _, _, verdict in collected}
    assert isinstance(by_id["q1"], ClarificationVerdict)
    assert isinstance(by_id["q3"], ClarificationVerdict)
    assert isinstance(by_id["q0"], JudgeVerdict)
    assert judge.calls == 2, "only the two answered pairs reached the judge"


def test_the_clarification_score_is_the_owners_fixed_value() -> None:
    verdict = ClarificationVerdict(question_id="q1", clarification_text="Which one?")

    assert CLARIFICATION_SCORE == 2.5
    assert verdict.overall_score == 2.5
    assert "declined to answer" in verdict.rationale
    # No dimensions were invented to average out at 2.5.
    assert not hasattr(verdict, "factual_correctness")


# --- scoring ------------------------------------------------------------------


def _row(qid, em_result, verdict):
    return (
        {"question_id": qid, "domain": "crm", "tier": "T1"},
        JudgeRequest(
            question="q",
            expected_answer="72",
            judge_reference="There are 72.",
            platform_answer="",
        ),
        verdict,
        ExactMatchOutcome(em_result),
    )


def _verdict(score: int = 4) -> JudgeVerdict:
    from judge.contracts import DIMENSIONS

    return JudgeVerdict(
        dimension_rationales=dict.fromkeys(DIMENSIONS, "r"),
        factual_correctness=score,
        completeness=score,
        format_adherence=score,
        sql_plausibility=score,
        prompt_version="p",
        model_version="m",
    )


def _mixed_results():
    """2 pass, 1 fail, 1 clarification — the shape that exposes the denominators."""

    return [
        _row("q0", ExactMatchResult.PASS, _verdict(4)),
        _row("q1", ExactMatchResult.PASS, _verdict(4)),
        _row("q2", ExactMatchResult.FAIL, _verdict(1)),
        _row("q3", ExactMatchResult.CLARIFICATION, ClarificationVerdict()),
    ]


def test_a_clarification_leaves_the_exact_match_denominator() -> None:
    summary = _summarise(_mixed_results())

    em = summary["exact_match"]
    assert em["eligible"] == 3, "the clarification is not a pass or a fail"
    assert em["clarification"] == 1
    assert em["pass_pct"] == 2 / 3 * 100
    # Named, not derived — §14.2 condition 4.
    assert "clarification" in em


def test_the_two_judge_means_report_their_own_denominators() -> None:
    summary = _summarise(_mixed_results())

    judge = summary["judge"]
    # Dimensions: only the 3 judged rows, (4+4+1)/3.
    assert judge["dimension_scored"] == 3
    assert judge["mean_per_dimension"]["factual_correctness"] == 3.0
    # Overall: the judged rows AND the clarification, (4+4+1+2.5)/4.
    assert judge["overall_scored"] == 4
    assert judge["mean_overall"] == 2.875
    assert summary["clarifications"] == 1


def test_a_clarification_is_not_counted_as_a_judge_error() -> None:
    summary = _summarise(_mixed_results())

    assert summary["judge_errors"] == 0
    assert summary["platform_errors"] == 0
    assert summary["judged"] == 3


# --- the scorecard ------------------------------------------------------------


def test_the_scorecard_names_the_exclusion_and_both_denominators() -> None:
    agg = aggregate(_mixed_results())
    crm = agg["crm"].overall

    assert crm.exact_match_eligible == 3
    assert crm.exact_match_clarification == 1
    assert crm.exact_match_pct == round(2 / 3 * 100, 4)
    assert crm.clarifications == 1
    # dim_mean over 3 judged rows; judge_overall over those 3 plus the 2.5.
    assert crm.dim_mean("factual_correctness") == 3.0
    assert crm.judge_overall == 2.875

    ctx = RunContext("r", "2026-01-01T00:00:00+00:00", "pv", "dv")
    rows, _ = build_summary_rows(_mixed_results(), ctx)
    domain_row = next(r for r in rows if r["tier"] == "ALL")
    assert domain_row["exact_match_clarification"] == 1
    assert domain_row["judge_clarifications"] == 1
    assert domain_row["judge_overall"] == 2.875


def test_judge_overall_is_unchanged_when_no_pair_clarified() -> None:
    """The new accumulator must reproduce the old mean-of-dimensions exactly."""

    results = [
        _row("q0", ExactMatchResult.PASS, _verdict(5)),
        _row("q1", ExactMatchResult.FAIL, _verdict(2)),
    ]
    crm = aggregate(results)["crm"].overall

    assert crm.judge_overall == 3.5
    assert crm.clarifications == 0
    assert crm.exact_match_clarification == 0


def test_a_platform_error_is_still_distinct_from_a_clarification() -> None:
    """An outage and a declined answer are different findings, not one bucket."""

    results = [
        _row("q0", ExactMatchResult.PASS, _verdict(4)),
        _row("q1", ExactMatchResult.CLARIFICATION, ClarificationVerdict()),
        _row("q2", ExactMatchResult.ERROR, PlatformError("500")),
    ]
    crm = aggregate(results)["crm"].overall

    assert crm.platform_errors == 1
    assert crm.clarifications == 1
    assert crm.exact_match_clarification == 1
    assert crm.exact_match_eligible == 1
    # The error row is not judged at all; the clarification contributes 2.5.
    assert crm.judge_overall == round((4 + 2.5) / 2, 4)
