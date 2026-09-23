"""The §11.3 PDF summary is read by people deciding things — test what it says.

`pypdf` is not a project dependency, so text-extraction tests skip without it.
The structural assertions (that the writer runs, paginates, and colours) do not
need it and always run.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from judge.contracts import DIMENSIONS, ClarificationVerdict, JudgeVerdict
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from scorecard import report
from scorecard.report import (
    ACCURACY_GATE_PCT,
    _provenance,
    _warnings,
    write_scorecard_pdf,
)
from scorecard.summary import RunContext, build_summary_rows


def _verdict(score: int = 4) -> JudgeVerdict:
    return JudgeVerdict(
        dimension_rationales=dict.fromkeys(DIMENSIONS, "r"),
        factual_correctness=score,
        completeness=score,
        format_adherence=score,
        sql_plausibility=score,
        prompt_version="p",
        model_version="m",
    )


def _row(tier: str, result: ExactMatchResult, verdict) -> tuple:
    return (
        {"question_id": "q", "domain": "crm", "tier": tier},
        SimpleNamespace(
            question="q",
            expected_answer="1",
            platform_answer="1",
            generated_sql="SELECT 1",
        ),
        verdict,
        ExactMatchOutcome(result),
    )


def _results(*, passes: int, fails: int, clarifications: int = 0) -> list:
    rows = [_row("T1", ExactMatchResult.PASS, _verdict(5)) for _ in range(passes)]
    rows += [_row("T1", ExactMatchResult.FAIL, _verdict(2)) for _ in range(fails)]
    rows += [
        _row("T1", ExactMatchResult.CLARIFICATION, ClarificationVerdict())
        for _ in range(clarifications)
    ]
    return rows


def _ctx(**overrides) -> RunContext:
    base = {
        "run_id": "20260923T140000Z",
        "run_timestamp_iso": "2026-09-23T14:00:00+00:00",
        "platform_version": "pulse-2026.09",
        "dataset_version": "dataset-v1.0.0+qa-pairs-v0.3.0",
    }
    base.update(overrides)
    return RunContext(**base)


def _text(tmp_path: Path, results: list, ctx: RunContext, **kwargs) -> str:
    pypdf = pytest.importorskip("pypdf")
    path = write_scorecard_pdf(tmp_path, results, ctx, **kwargs)
    return "\n".join(
        page.extract_text() for page in pypdf.PdfReader(str(path)).pages
    )


# --- the bug: snake_case identifiers were being mangled -----------------------


def test_field_names_keep_their_underscores(tmp_path: Path) -> None:
    """`.replace("_", "")` stripped markdown italics and identifiers alike.

    The provenance block printed `runid`, `platformversion`, `datasetversion`.
    A provenance block whose field names are wrong is worse than none.
    """

    text = _text(tmp_path, _results(passes=5, fails=5), _ctx())

    assert "run_id" in text
    assert "platform_version" in text
    assert "dataset_version" in text
    assert "run_timestamp_iso" in text
    assert "runid" not in text
    assert "platformversion" not in text
    assert "datasetversion" not in text


def test_provenance_is_built_as_data_not_scraped_prose() -> None:
    labels = [label for label, _ in _provenance(_ctx())]

    assert labels[:4] == [
        "run_id",
        "run_timestamp_iso",
        "platform_version",
        "dataset_version",
    ]


def test_absent_versions_say_so_rather_than_printing_a_dash() -> None:
    values = dict(_provenance(_ctx(platform_version="", dataset_version="")))

    assert values["platform_version"] == "not supplied"
    assert values["dataset_version"] == "not supplied"


# --- the headline: the number against the gate --------------------------------


def test_a_failing_run_says_below_gate_on_its_face(tmp_path: Path) -> None:
    text = _text(tmp_path, _results(passes=88, fails=72), _ctx())

    assert "BELOW GATE" in text
    assert f"vs {ACCURACY_GATE_PCT:.0f}% gate" in text
    assert "88 of 160" in text


def test_a_passing_run_says_pass(tmp_path: Path) -> None:
    text = _text(tmp_path, _results(passes=96, fails=4), _ctx())

    assert "PASS" in text
    assert "BELOW GATE" not in text


# --- warnings -----------------------------------------------------------------


def test_below_the_gate_raises_a_warning() -> None:
    rows, comparisons = build_summary_rows(_results(passes=1, fails=1), _ctx())

    alerts = _warnings(_ctx(), rows, comparisons)

    assert any("ACCURACY GATE" in a and "§14.2" in a for a in alerts)


def test_at_the_gate_raises_no_accuracy_warning() -> None:
    results = _results(passes=96, fails=4)  # 96%
    ctx = _ctx(scorecard_mode="RELEASE", calibrated=True)
    rows, comparisons = build_summary_rows(results, ctx)

    alerts = _warnings(ctx, rows, comparisons)

    assert not any("ACCURACY GATE" in a for a in alerts)
    # A calibrated RELEASE run with nothing wrong should be quiet.
    assert alerts == []


def test_clarifications_are_flagged_as_a_denominator_exclusion() -> None:
    results = _results(passes=5, fails=5, clarifications=3)
    rows, comparisons = build_summary_rows(results, _ctx())

    alerts = _warnings(_ctx(), rows, comparisons)

    exclusion = next(a for a in alerts if "declined" in a)
    assert "EXCLUDED" in exclusion
    assert "§14.2 condition 4" in exclusion
    assert "3 question(s)" in exclusion


def test_preview_and_uncalibrated_are_both_called_out() -> None:
    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    alerts = _warnings(_ctx(scorecard_mode="PREVIEW", calibrated=False), rows, comparisons)

    assert any("PREVIEW" in a for a in alerts)
    assert any("UNCALIBRATED" in a for a in alerts)


def test_a_non_default_comparison_is_called_out() -> None:
    ctx = _ctx(comparison_is_default=False, scorecard_mode="RELEASE", calibrated=True)
    rows, comparisons = build_summary_rows(_results(passes=96, fails=4), ctx)

    assert any("NON-DEFAULT" in a for a in _warnings(ctx, rows, comparisons))


def test_a_merge_note_is_surfaced_not_buried(tmp_path: Path) -> None:
    ctx = _ctx(provenance_note="55 of 160 rows from re-run 20260921T153322Z")

    text = _text(tmp_path, _results(passes=88, fails=72), ctx)

    assert "Assembled: 55 of 160 rows" in text


def test_determinism_failure_is_stated_plainly() -> None:
    values = dict(_provenance(_ctx(judge_seed_enforced=False)))

    assert "seed never reached provider" in values["determinism"]


def test_enforced_determinism_says_so() -> None:
    values = dict(_provenance(_ctx()))

    assert values["determinism"] == "temperature 0 + seed enforced"


# --- structure ----------------------------------------------------------------


def test_the_two_scores_are_split_into_labelled_sections(tmp_path: Path) -> None:
    """18 columns at 7.5pt was complete and unreadable. §11.3 wants them apart."""

    text = _text(tmp_path, _results(passes=5, fails=5), _ctx())

    assert "Deterministic accuracy" in text
    assert "Judge quality" in text
    # ...and never blended into one number.
    assert "never" in text and "composite" in text


def test_the_pdf_is_written_even_with_a_single_result(tmp_path: Path) -> None:
    path = write_scorecard_pdf(tmp_path, _results(passes=1, fails=0), _ctx())

    assert path.name == "scorecard.pdf"
    assert path.stat().st_size > 1000


# --- §14.2 gate checklist -----------------------------------------------------


def test_the_checklist_covers_all_nine_conditions() -> None:
    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    checks = report.gate_checklist(_ctx(), rows, comparisons)

    assert len(checks) == 9
    assert [c[0][:2] for c in checks] == [f"{n}." for n in range(1, 10)]


def test_condition_one_fails_below_the_gate_and_names_the_domain() -> None:
    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    condition, verdict, note = report.gate_checklist(_ctx(), rows, comparisons)[0]

    assert verdict == "FAIL"
    assert "crm" in note


def test_condition_one_passes_at_the_gate() -> None:
    rows, comparisons = build_summary_rows(_results(passes=96, fails=4), _ctx())

    assert report.gate_checklist(_ctx(), rows, comparisons)[0][1] == "PASS"


def test_unobservable_conditions_are_marked_manual_not_assumed_passed() -> None:
    """§15 is a process. Claiming PASS for it would be a lie."""

    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    checks = {c[0][:2]: c[1] for c in report.gate_checklist(_ctx(), rows, comparisons)}

    assert checks["3."] == "MANUAL"  # independent reviewer
    assert checks["6."] == "MANUAL"  # cross-domain uniqueness
    assert checks["8."] == "MANUAL"  # clean-room reproducibility


def test_the_baseline_condition_tracks_the_scorecard_mode() -> None:
    results = _results(passes=96, fails=4)
    preview = _ctx(scorecard_mode="PREVIEW")
    release = _ctx(scorecard_mode="RELEASE", calibrated=True)

    rows, comparisons = build_summary_rows(results, preview)
    assert report.gate_checklist(preview, rows, comparisons)[6][1] == "NOT YET"

    rows, comparisons = build_summary_rows(results, release)
    assert report.gate_checklist(release, rows, comparisons)[6][1] == "PASS"


def test_condition_four_asks_for_review_when_questions_are_excluded() -> None:
    """Exclusions are legitimate but must be looked at, per §14.2 condition 4."""

    results = _results(passes=5, fails=5, clarifications=2)
    rows, comparisons = build_summary_rows(results, _ctx())

    _c, verdict, note = report.gate_checklist(_ctx(), rows, comparisons)[3]

    assert verdict == "REVIEW"
    assert "2 question(s)" in note


def test_condition_nine_is_about_crm_specifically() -> None:
    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    _c, verdict, note = report.gate_checklist(_ctx(), rows, comparisons)[8]

    assert verdict == "FAIL"
    assert "crm is below the gate" in note


# --- §9.1 tier quota ----------------------------------------------------------


def test_a_tier_at_quota_reads_ok() -> None:
    rows = [
        {"domain": "crm", "tier": "T1", "questions_total": 32},
        {"domain": "crm", "tier": "T2", "questions_total": 40},
    ]

    assert report.tier_quota_rows(rows) == [
        ("crm", "T1", 32, 32, "ok"),
        ("crm", "T2", 40, 40, "ok"),
    ]


def test_a_tier_short_of_quota_is_flagged() -> None:
    """A pair that left the set raises the percentage without improving anything."""

    rows = [{"domain": "crm", "tier": "T1", "questions_total": 30}]

    assert report.tier_quota_rows(rows) == [("crm", "T1", 30, 32, "SHORT")]


def test_the_roll_up_row_is_not_compared_to_a_tier_quota() -> None:
    rows = [{"domain": "crm", "tier": "ALL", "questions_total": 160}]

    assert report.tier_quota_rows(rows) == []


# --- failure kinds ------------------------------------------------------------


def _failing(detail: str):
    return (
        {"question_id": "q", "domain": "crm", "tier": "T1"},
        SimpleNamespace(
            question="q", expected_answer="1", platform_answer="2", generated_sql="s"
        ),
        _verdict(2),
        ExactMatchOutcome(ExactMatchResult.FAIL, detail),
    )


def test_failures_group_by_kind_not_by_the_missing_value() -> None:
    """`value '5' not present` and `value '0' not present` are one finding."""

    results = [
        _failing("value '5' not present"),
        _failing("value '0' not present"),
        _failing("value '23184584.00' not present"),
        _failing("label 'Standard' present but a different label claims its value"),
    ]

    total, reasons = report.failure_reasons(results)

    assert total == 4
    assert reasons[0] == (3, "value <value> not present")
    assert len(reasons) == 2


def test_passes_and_clarifications_are_not_counted_as_failures() -> None:
    results = _results(passes=5, fails=0, clarifications=3)

    total, reasons = report.failure_reasons(results)

    assert total == 0
    assert reasons == []


def test_a_failure_with_no_detail_is_still_counted() -> None:
    total, reasons = report.failure_reasons([_failing("")])

    assert total == 1
    assert reasons == [(1, "no detail recorded")]


# --- status colour, from the validated palette --------------------------------


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (100.0, report._STATUS_GOOD),
        (95.0, report._STATUS_GOOD),
        (94.9, report._STATUS_WARNING),
        (75.0, report._STATUS_WARNING),
        (60.0, report._STATUS_SERIOUS),
        (0.0, report._STATUS_CRITICAL),
        (None, report._INK_MUTED),
    ],
)
def test_status_bands_against_the_gate(pct, expected) -> None:
    assert report._status_fill(pct) == expected


# --- the chart ----------------------------------------------------------------


def test_the_tier_chart_is_drawn_per_domain() -> None:
    rows, _ = build_summary_rows(_results(passes=5, fails=5), _ctx())

    drawing = report._tier_chart(rows, "crm", width=400, height=100)

    assert drawing is not None
    assert drawing.width == 400


def test_no_chart_for_a_domain_with_no_tier_rows() -> None:
    rows, _ = build_summary_rows(_results(passes=5, fails=5), _ctx())

    assert report._tier_chart(rows, "sales", width=400, height=100) is None


# --- it all lands in the document --------------------------------------------


def test_the_new_sections_reach_the_pdf(tmp_path: Path) -> None:
    results = _results(passes=20, fails=12, clarifications=0)
    text = _text(tmp_path, results, _ctx())

    assert "Accuracy gate (§14.2)" in text
    assert "Exact-match by tier" in text
    assert "95% gate" in text
    assert "§9.1 quota" in text
    assert "Where the failures are" in text
