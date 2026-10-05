"""Anchor authoring — §10.2 needs graded anchors, and nothing produced any."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from judge.anchors_io import (
    SCORE_COLUMNS,
    SHEET_COLUMNS,
    AnchorExportError,
    export_candidates,
    import_grades,
)
from judge.calibration import AnchorSetTooWeak
from judge.contracts import DIMENSIONS

# A spread that satisfies §10.2: ≥3 distinct scores per dimension, and at least
# one factual_correctness=1 — the platform returning a WRONG NUMBER, which is
# the only anchor that can catch a judge unable to say 1.
_SPREAD = [1, 3, 5, 2, 4, 1, 5, 3, 2, 4, 5, 1]


RELEASE_VERSION = "v1.0.0"


def _run(tmp_path: Path, rows: list[dict], run_id: str = "20260101T000000Z") -> Path:
    """A scored run inside a release bundle, in the canonical layout.

    The bundle is version-first — release/<version>/<domain>/judge/<run_id> — and
    `component_dir` resolves it from the domain's generation config, so the
    fixture has to provide that config too. Writing the run directory alone
    passed while the layout was domain-first and stopped the day it changed.
    """

    config = tmp_path / "config" / "generation" / "crm"
    config.mkdir(parents=True, exist_ok=True)
    (config / "release.json").write_text(
        json.dumps({"domain": "crm", "release_version": RELEASE_VERSION}),
        encoding="utf-8",
    )
    run_dir = tmp_path / "release" / RELEASE_VERSION / "crm" / "judge" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return run_dir


def _rows(n: int) -> list[dict]:
    outcomes = ["pass", "fail", "clarification"]
    tiers = ["T1", "T2", "T3", "T4", "T5"]
    return [
        {
            "question_id": f"CRM-T{i % 5 + 1}-{i:03d}",
            "tier": tiers[i % 5],
            "natural_language_question": f"question {i}?",
            "expected_answer": str(100 + i),
            "actual_answer": f"the answer is {100 + i}",
            "platform_generated_sql": "SELECT 1",
            "exact_match_result": outcomes[i % 3],
        }
        for i in range(n)
    ]


def _fill(sheet: Path, score) -> Path:
    rows = list(csv.DictReader(sheet.open(newline="", encoding="utf-8")))
    for index, row in enumerate(rows):
        for column in SCORE_COLUMNS:
            row[column] = str(score(index))
        for dimension in DIMENSIONS:
            row[f"rationale_{dimension}"] = "graded in session"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return sheet


def test_the_sheet_carries_evidence_and_no_judge_opinion(tmp_path, monkeypatch):
    """§10.2's blindness runs both ways: a grader shown the judge's 4 returns a 4,
    and the whole measurement is agreement between independent opinions."""

    rows = _rows(20)
    for row in rows:  # a real run also records what the judge concluded
        row["judge_scores"] = {d: 4 for d in DIMENSIONS}
        row["judge_overall"] = 4.0
    _run(tmp_path, rows)

    sheet = export_candidates("crm", count=12, repo_root=tmp_path)
    header = next(csv.reader(sheet.open(newline="", encoding="utf-8")))

    assert "judge_reference" in header  # the pair's ideal answer IS evidence
    assert not [c for c in header if c.startswith("judge_") and c != "judge_reference"]
    assert "exact_match_result" in header  # deterministic fact, not an opinion


def test_candidates_are_spread_across_outcome_and_tier(tmp_path):
    """Taking the first N rows would reproduce the provisional set's flaw: T1
    scalars that everything gets right."""

    _run(tmp_path, _rows(60))
    sheet = export_candidates("crm", count=12, repo_root=tmp_path)
    rows = list(csv.DictReader(sheet.open(newline="", encoding="utf-8")))

    assert len({r["tier"] for r in rows}) >= 4
    assert len({r["exact_match_result"] for r in rows}) >= 2
    # Wrong-number rows are what catch a judge that never scores 1, so they must
    # not be crowded out by passes.
    assert sum(1 for r in rows if r["exact_match_result"] == "fail") >= 3


def test_export_is_deterministic(tmp_path):
    """Re-exporting must not reshuffle the sheet under a part-way grading session."""

    _run(tmp_path, _rows(40))
    first = export_candidates("crm", count=12, repo_root=tmp_path).read_text()
    second = export_candidates("crm", count=12, repo_root=tmp_path).read_text()
    assert first == second


def test_a_constant_graded_set_is_refused_before_any_budget_is_spent(tmp_path):
    """The exact flaw that quarantined crm.provisional.json: a judge that ignores
    its input and always answers 4 would pass it."""

    _run(tmp_path, _rows(40))
    sheet = _fill(export_candidates("crm", count=12, repo_root=tmp_path), lambda i: 4)

    with pytest.raises(AnchorSetTooWeak, match="distinct human score"):
        import_grades("crm", sheet, output=tmp_path / "anchors.json")
    assert not (tmp_path / "anchors.json").exists(), "nothing is written on refusal"


def test_a_spanning_set_is_written_in_the_shape_the_loader_expects(tmp_path):
    _run(tmp_path, _rows(40))
    sheet = _fill(
        export_candidates("crm", count=12, repo_root=tmp_path),
        lambda i: _SPREAD[i % len(_SPREAD)],
    )

    out = import_grades("crm", sheet, output=tmp_path / "anchors.json")
    anchors = json.loads(out.read_text(encoding="utf-8"))

    assert len(anchors) == 12
    first = anchors[0]
    assert set(first["human_scores"]) == set(DIMENSIONS)
    assert all(1 <= v <= 5 for v in first["human_scores"].values())
    assert first["question_id"] and first["platform_answer"]
    assert 1 in [a["human_scores"]["factual_correctness"] for a in anchors]


def test_a_partly_graded_sheet_names_the_row_it_stopped_on(tmp_path):
    """Silently treating a blank as a zero, or skipping the row, would quietly
    shrink the set below the §10.2 minimum."""

    _run(tmp_path, _rows(40))
    sheet = _fill(
        export_candidates("crm", count=12, repo_root=tmp_path),
        lambda i: _SPREAD[i % len(_SPREAD)],
    )
    rows = list(csv.DictReader(sheet.open(newline="", encoding="utf-8")))
    rows[3]["human_completeness"] = ""
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(AnchorExportError, match="human_completeness is blank"):
        import_grades("crm", sheet, output=tmp_path / "anchors.json")


def test_an_out_of_range_grade_is_rejected_not_clamped(tmp_path):
    _run(tmp_path, _rows(40))
    sheet = _fill(export_candidates("crm", count=12, repo_root=tmp_path), lambda i: 7)

    with pytest.raises(AnchorExportError, match="outside 1-5"):
        import_grades("crm", sheet, output=tmp_path / "anchors.json")


def test_force_writes_a_weak_set_as_a_work_in_progress(tmp_path):
    """Escape hatch for a part-finished session — and calibration still refuses
    the set, so it cannot become a marker by accident."""

    _run(tmp_path, _rows(40))
    sheet = _fill(export_candidates("crm", count=12, repo_root=tmp_path), lambda i: 4)

    out = import_grades("crm", sheet, output=tmp_path / "anchors.json", force=True)
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 12


def test_no_scored_run_says_how_to_make_one(tmp_path):
    """A CONFIGURED domain with nothing scored yet — the error must name the
    command that produces a run, not complain about the release config."""

    _run(tmp_path, _rows(1))                      # creates the release config
    import shutil
    shutil.rmtree(tmp_path / "release")           # ...then remove every run

    with pytest.raises(AnchorExportError, match="python main.py judge --domain crm"):
        export_candidates("crm", repo_root=tmp_path)
