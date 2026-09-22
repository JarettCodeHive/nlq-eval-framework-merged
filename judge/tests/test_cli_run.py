"""Live-run orchestration — concurrent platform calls + per-question fault isolation."""

from __future__ import annotations

import asyncio
import time

from judge.cli import PlatformError, _collect_and_score
from judge.heuristic_judge import HeuristicJudge
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from judge.contracts import PulseResponse
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


def _run(pairs, pulse, *, pulse_concurrency=4, judge_concurrency=4, judge=None):
    return asyncio.run(
        _collect_and_score(
            pairs,
            pulse,
            judge or HeuristicJudge(),
            pulse_concurrency=pulse_concurrency,
            judge_concurrency=judge_concurrency,
        )
    )


def test_one_platform_failure_does_not_abort_the_batch():
    pulse = _FlakyPulse(fail={"q3", "q7"})
    collected = _run(_pairs(10), pulse)

    assert len(collected) == 10
    errored = {pair["question_id"] for pair, _, err, _ in collected if err}
    assert errored == {"q3", "q7"}
    for pair, req, err, verdict in collected:
        if pair["question_id"] in errored:
            assert req.platform_answer == "" and req.generated_sql is None
            assert "pulse exploded" in err
            # Nothing to score when the platform never answered.
            assert verdict is None
        else:
            assert req.platform_answer == "72 things"
            assert err is None
            assert verdict is not None


def test_a_judge_failure_is_isolated_per_row():
    """A bad verdict must not discard the platform answers already paid for."""

    class _BoomJudge(HeuristicJudge):
        async def judge(self, req):
            if req.question_id == "q2":
                raise RuntimeError("judge exploded")
            return await super().judge(req)

    collected = _run(_pairs(4), _FlakyPulse(fail=set()), judge=_BoomJudge())
    by_id = {pair["question_id"]: (err, v) for pair, _, err, v in collected}
    assert isinstance(by_id["q2"][1], Exception)
    assert by_id["q2"][0] is None, "a judge failure is not a platform error"
    assert all(
        not isinstance(by_id[f"q{i}"][1], Exception) for i in (0, 1, 3)
    ), "other rows keep their verdicts"


def test_platform_calls_run_concurrently():
    pulse = _FlakyPulse(fail=set(), delay=0.1)
    start = time.monotonic()
    _run(_pairs(12), pulse, pulse_concurrency=6)
    elapsed = time.monotonic() - start
    # 12 calls * 0.1s serial = 1.2s; at concurrency 6 it should be well under.
    assert elapsed < 0.6


def test_scoring_overlaps_fetching():
    """The whole point of the pipeline: the judge leg hides inside the platform
    wait instead of being added to it."""

    class _SlowJudge(HeuristicJudge):
        async def judge(self, req):
            await asyncio.sleep(0.1)
            return await super().judge(req)

    pulse = _FlakyPulse(fail=set(), delay=0.1)
    start = time.monotonic()
    _run(_pairs(8), pulse, pulse_concurrency=4, judge_concurrency=4, judge=_SlowJudge())
    elapsed = time.monotonic() - start
    # Sequential phases would be 8/4*0.1 (fetch) + 8/4*0.1 (judge) = 0.4s.
    # Pipelined, the judge time overlaps the next fetches.
    assert elapsed < 0.38, f"phases did not overlap (took {elapsed:.3f}s)"


def test_sql_pulse_is_forced_serial():
    class _SqlPulse(_FlakyPulse):
        mode = "sql[crm]"

    pulse = _SqlPulse(fail=set(), delay=0.05)
    start = time.monotonic()
    _run(_pairs(6), pulse, pulse_concurrency=8)
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
            ExactMatchOutcome(em),
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


def test_release_refuses_a_non_live_answer_source():
    """HC-4: all evaluation goes through the platform.

    A baseline built from --pulse sql would measure our own reference SQL
    replayed against our own dataset — it would look like an accuracy number
    and mean nothing about the system under evaluation.
    """
    from judge.cli import _resolve_scorecard_mode, build_argparser

    args = build_argparser().parse_args(
        [
            "--release",
            "--judge",
            "llm",
            "--pulse",
            "sql",
            "--platform-version",
            "v1",
            "--dataset-version",
            "d1",
        ]
    )
    mode, blockers = _resolve_scorecard_mode(args, calibrated=True)
    assert mode == "RELEASE"
    assert any("--pulse live" in b and "HC-4" in b for b in blockers)


def test_release_accepts_live_when_everything_else_is_satisfied():
    from judge.cli import _resolve_scorecard_mode, build_argparser

    args = build_argparser().parse_args(
        [
            "--release",
            "--judge",
            "llm",
            "--pulse",
            "live",
            "--platform-version",
            "v1",
            "--dataset-version",
            "d1",
        ]
    )
    mode, blockers = _resolve_scorecard_mode(args, calibrated=True)
    assert mode == "RELEASE"
    assert blockers == []


def test_heuristic_judge_is_never_release_eligible():
    from judge.cli import _resolve_scorecard_mode, build_argparser

    args = build_argparser().parse_args(
        [
            "--release",
            "--judge",
            "heuristic",
            "--pulse",
            "live",
            "--platform-version",
            "v1",
            "--dataset-version",
            "d1",
        ]
    )
    _, blockers = _resolve_scorecard_mode(args, calibrated=True)
    assert any("--judge llm" in b for b in blockers)
