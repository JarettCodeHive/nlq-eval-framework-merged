"""Regression scorecard — §11.1 summary, §11.2 drill-down, §11.3 baseline rules."""

from __future__ import annotations

import csv
import os
from types import SimpleNamespace

import pytest

from judge.contracts import JudgeVerdict
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from scorecard.baseline import (
    REGRESSION_DELTA_PP,
    BaselineExists,
    compare_to_baseline,
    establish_baseline,
    load_baseline,
)
from scorecard.summary import (
    QUESTION_FIELDNAMES,
    SUMMARY_FIELDNAMES,
    RunContext,
    aggregate,
    build_summary_rows,
    write_question_results_csv,
    write_scorecard_summary_csv,
)


def _verdict(fc=5, comp=4, fmt=5, sql=5) -> JudgeVerdict:
    return JudgeVerdict(
        dimension_rationales={
            "factual_correctness": "r",
            "completeness": "r",
            "format_adherence": "r",
            "sql_plausibility": "r",
        },
        factual_correctness=fc,
        completeness=comp,
        format_adherence=fmt,
        sql_plausibility=sql,
        prompt_version="p",
        model_version="m",
    )


def _result(
    domain,
    tier,
    qid,
    em,
    verdict,
    *,
    sql="SELECT 1",
    rephrase=None,
    null_handling="not_applicable",
):
    """One row as `judge.cli` builds it: the 4th slot is an ExactMatchOutcome,
    which carries the failure reason §11.2 needs, not a bare enum."""
    pair = {
        "question_id": qid,
        "domain": domain,
        "tier": tier,
        "natural_language_question": f"q-{qid}",
        "expected_answer": "72",
        "rephrase_group_id": rephrase,
        "null_handling": null_handling,
        "null_handling_detail": "",
    }
    req = SimpleNamespace(
        question=f"q-{qid}",
        expected_answer="72",
        platform_answer="72" if em == ExactMatchResult.PASS else "0",
        generated_sql=sql,
    )
    outcome = em if isinstance(em, ExactMatchOutcome) else ExactMatchOutcome(em)
    return (pair, req, verdict, outcome)


def _ctx(mode="PREVIEW", **kw):
    return RunContext(
        run_id="20260101T000000Z",
        run_timestamp_iso="2026-01-01T00:00:00+00:00",
        platform_version=kw.get("platform_version", "pulse-1"),
        dataset_version=kw.get("dataset_version", "ds-1"),
        scorecard_mode=mode,
        calibrated=kw.get("calibrated", True),
    )


def _sample_results():
    return [
        _result("crm", "T1", "crm-t1-001", ExactMatchResult.PASS, _verdict(comp=4)),
        _result("crm", "T1", "crm-t1-002", ExactMatchResult.FAIL, _verdict(comp=2)),
        _result(
            "crm", "T5", "crm-t5-001", ExactMatchResult.NOT_APPLICABLE, _verdict(comp=3)
        ),
        _result("sales", "T2", "sales-t2-001", ExactMatchResult.PASS, _verdict()),
    ]


def test_aggregate_groups_by_domain_and_tier():
    agg = aggregate(_sample_results())
    assert set(agg) == {"crm", "sales"}
    assert set(agg["crm"].tiers) == {"T1", "T5"}
    # crm overall: 1 pass out of 2 eligible (the NOT_APPLICABLE T5 pair is excluded)
    assert agg["crm"].overall.exact_match_eligible == 2
    assert agg["crm"].overall.exact_match_pass == 1
    assert agg["crm"].overall.exact_match_pct == 50.0
    # judge mean over all three judged crm pairs
    assert agg["crm"].overall.dim_mean("completeness") == pytest.approx(
        (4 + 2 + 3) / 3, rel=1e-6
    )


def test_summary_csv_has_contract_columns_and_domain_plus_tier_rows(tmp_path):
    path = write_scorecard_summary_csv(tmp_path, _sample_results(), _ctx())
    with path.open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == SUMMARY_FIELDNAMES
        rows = list(reader)
    for col in (
        "run_id",
        "run_timestamp_iso",
        "platform_version",
        "dataset_version",
        "baseline_exact_match_pct",
        "delta_pct",
        "regression_flag",
        "judge_factual",
        "judge_sql",
    ):
        assert col in reader.fieldnames

    crm_rows = [r for r in rows if r["domain"] == "crm"]
    assert {r["tier"] for r in crm_rows} == {"ALL", "T1", "T5"}
    # No baseline yet → the comparison columns are blank everywhere.
    assert all(r["baseline_exact_match_pct"] == "" for r in rows)
    assert all(r["regression_flag"] == "" for r in rows)


def test_question_results_csv_one_row_per_question_with_sql_always(tmp_path):
    results = _sample_results()
    # one judged pair replaced with a judge error
    results.append(
        (
            {
                "question_id": "crm-t1-003",
                "domain": "crm",
                "tier": "T1",
                "natural_language_question": "q",
                "rephrase_group_id": "g1",
            },
            SimpleNamespace(
                question="q",
                expected_answer="5",
                platform_answer="5",
                generated_sql="SELECT 5",
            ),
            RuntimeError("judge blew up"),
            ExactMatchOutcome(ExactMatchResult.PASS),
        )
    )
    path = write_question_results_csv(tmp_path, results, _ctx())
    with path.open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == QUESTION_FIELDNAMES
        rows = list(reader)
    assert len(rows) == len(results)
    # §11.2 — generated SQL logged for every question
    assert all(r["platform_generated_sql"] for r in rows)
    err_row = next(r for r in rows if r["question_id"] == "crm-t1-003")
    assert "judge blew up" in err_row["judge_error"]
    assert err_row["judge_factual"] == ""
    assert err_row["rephrase_group_id"] == "g1"


def test_establish_baseline_is_write_once_and_immutable(tmp_path):
    bdir = tmp_path / "baselines"
    p = establish_baseline(
        "pulse-2026.09",
        {"crm": 96.0, "sales": 91.0},
        run_id="r1",
        run_timestamp_iso="2026-01-01T00:00:00+00:00",
        dataset_version="ds-1",
        baseline_dir=bdir,
    )
    assert p.is_file()
    assert not os.access(p, os.W_OK)  # read-only
    doc = load_baseline("pulse-2026.09", baseline_dir=bdir)
    assert doc["domains"] == {"crm": 96.0, "sales": 91.0}

    with pytest.raises(BaselineExists):
        establish_baseline(
            "pulse-2026.09",
            {"crm": 99.0},
            run_id="r2",
            run_timestamp_iso="2026-02-01T00:00:00+00:00",
            dataset_version="ds-2",
            baseline_dir=bdir,
        )


def test_regression_flag_triggers_at_five_point_drop():
    baseline = {"domains": {"crm": 95.0, "sales": 90.0, "finance": 80.0}}
    current = {
        "crm": 95.0 + REGRESSION_DELTA_PP,  # exactly -5.0 → flag
        "sales": 86.0,  # -4.0 → no flag
        "finance": None,  # missing → no flag
        "logistics": 70.0,  # no baseline → no flag
    }
    cmp = compare_to_baseline(baseline, current)
    assert cmp["crm"].regression_flag is True
    assert cmp["crm"].delta_pct == pytest.approx(-5.0)
    assert cmp["sales"].regression_flag is False
    assert cmp["finance"].regression_flag is False
    assert cmp["logistics"].regression_flag is False
    assert cmp["logistics"].has_baseline is False


def test_release_run_compares_against_existing_baseline(tmp_path):
    bdir = tmp_path / "baselines"
    establish_baseline(
        "pulse-1",
        {"crm": 100.0},
        run_id="r0",
        run_timestamp_iso="2026-01-01T00:00:00+00:00",
        dataset_version="ds-1",
        baseline_dir=bdir,
    )
    baseline = load_baseline("pulse-1", baseline_dir=bdir)
    # crm exact-match this run is 50% → 50 pp drop → flag
    _, comparisons = build_summary_rows(
        _sample_results(), _ctx("RELEASE"), baseline=baseline
    )
    assert comparisons["crm"].regression_flag is True
    assert comparisons["crm"].delta_pct == pytest.approx(-50.0)
