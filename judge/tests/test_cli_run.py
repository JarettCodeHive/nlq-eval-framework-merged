"""Live-run orchestration — concurrent platform calls + per-question fault isolation."""

from __future__ import annotations

import asyncio
import time

from judge.cli import PlatformError, _collect_requests
from judge.exact_match import ExactMatchResult
from judge.mock_pulse import PulseResponse
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


class _FlakyPulse:
    """Answers everyone except the ids in `fail`, which raise."""

    mode = "live"

    def __init__(self, fail: set[str], delay: float = 0.0):
        self.fail = fail
        self.delay = delay
        self.calls = 0

    def query(self, question_id: str) -> PulseResponse:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if question_id in self.fail:
            raise RuntimeError(f"pulse exploded on {question_id}")
        return PulseResponse(
            question_id=question_id, answer_text="72 things", generated_sql="SELECT 72"
        )


def test_one_platform_failure_does_not_abort_the_batch():
    pulse = _FlakyPulse(fail={"q3", "q7"})
    collected = asyncio.run(_collect_requests(_pairs(10), pulse, concurrency=4))

    assert len(collected) == 10
    errored = {pair["question_id"] for pair, _, err in collected if err}
    assert errored == {"q3", "q7"}
    for pair, req, err in collected:
        if pair["question_id"] in errored:
            assert req.platform_answer == "" and req.generated_sql is None
            assert "pulse exploded" in err
        else:
            assert req.platform_answer == "72 things"
            assert err is None


def test_platform_calls_run_concurrently():
    pulse = _FlakyPulse(fail=set(), delay=0.1)
    start = time.monotonic()
    asyncio.run(_collect_requests(_pairs(12), pulse, concurrency=6))
    elapsed = time.monotonic() - start
    # 12 calls * 0.1s serial = 1.2s; at concurrency 6 it should be well under.
    assert elapsed < 0.6


def test_sql_pulse_is_forced_serial():
    class _SqlPulse(_FlakyPulse):
        mode = "sql[crm]"

    pulse = _SqlPulse(fail=set(), delay=0.05)
    start = time.monotonic()
    asyncio.run(_collect_requests(_pairs(6), pulse, concurrency=8))
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25  # 6 * 0.05 serial, concurrency ignored


def test_scorecard_counts_platform_errors_out_of_band():
    from types import SimpleNamespace

    def row(qid, em, verdict):
        return (
            {
                "question_id": qid,
                "domain": "crm",
                "tier": "T1",
                "natural_language_question": "q",
            },
            SimpleNamespace(
                question="q",
                expected_answer="72",
                platform_answer="",
                generated_sql=None,
            ),
            verdict,
            em,
        )

    results = [
        row("q0", ExactMatchResult.PASS, _verdict()),
        row("q1", ExactMatchResult.FAIL, _verdict()),
        row("q2", ExactMatchResult.ERROR, PlatformError("PulseResponseError: 500")),
    ]
    agg = aggregate(results)
    crm = agg["crm"].overall
    assert crm.platform_errors == 1
    assert crm.exact_match_eligible == 2  # the ERROR row is excluded
    assert crm.exact_match_pct == 50.0

    ctx = RunContext("r", "2026-01-01T00:00:00+00:00", "pv", "dv")
    rows, _ = build_summary_rows(results, ctx)
    domain_row = next(r for r in rows if r["tier"] == "ALL")
    assert domain_row["platform_errors"] == 1


def _verdict():
    from judge.contracts import JudgeVerdict

    return JudgeVerdict(
        dimension_rationales={
            d: "r"
            for d in (
                "factual_correctness",
                "completeness",
                "format_adherence",
                "sql_plausibility",
            )
        },
        factual_correctness=5,
        completeness=5,
        format_adherence=5,
        sql_plausibility=5,
        prompt_version="p",
        model_version="m",
    )
