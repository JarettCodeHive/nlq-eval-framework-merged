from __future__ import annotations

from types import SimpleNamespace

from judge.input_contract import REQUIRED_FIELDS, load_input_csv
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from judge.contracts import JudgeVerdict
from scorecard.summary import RunContext, write_scorecard_summary_csv


def test_load_input_csv_validates_required_columns(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text(
        "question_id,domain,tier,natural_language_question,expected_answer,judge_reference,reference_sql\n"
        "q1,crm,T1,How many?,72,Answer,SELECT 1\n",
        encoding="utf-8",
    )
    rows = load_input_csv(path)
    assert len(rows) == 1
    assert rows[0].question_id == "q1"
    assert set(REQUIRED_FIELDS).issubset(
        path.read_text(encoding="utf-8").splitlines()[0].split(",")
    )


def test_write_scorecard_summary_csv_groups_by_domain_and_tier(tmp_path):
    verdict = JudgeVerdict(
        dimension_rationales={
            "factual_correctness": "ok",
            "completeness": "ok",
            "format_adherence": "ok",
            "sql_plausibility": "ok",
        },
        factual_correctness=5,
        completeness=4,
        format_adherence=5,
        sql_plausibility=5,
        prompt_version="p",
        model_version="m",
    )
    req = SimpleNamespace(
        question="q",
        expected_answer="72",
        platform_answer="72",
        generated_sql="SELECT 1",
    )
    results = [
        (
            {
                "question_id": "q1",
                "domain": "crm",
                "tier": "T1",
                "natural_language_question": "q",
            },
            req,
            verdict,
            ExactMatchOutcome(ExactMatchResult.PASS),
        )
    ]
    ctx = RunContext(
        run_id="r1",
        run_timestamp_iso="2026-01-01T00:00:00+00:00",
        platform_version="pulse-1",
        dataset_version="ds-1",
    )
    path = write_scorecard_summary_csv(tmp_path, results, ctx)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "exact_match_pct" in text
    assert "regression_flag" in text
