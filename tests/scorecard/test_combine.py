"""`score` combines per-domain judge runs into the one scorecard that owns the baseline.

The behaviour that matters is the refusals. A combined RELEASE scorecard asserts
that several runs describe one platform state, and every blocker here is part of
that assertion — get it wrong and you freeze a baseline that means nothing, in a
store that is deliberately write-once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scorecard import combine as mod
from scorecard.combine import (
    CombineError,
    collect,
    load_run,
    release_blockers,
)


def _row(qid: str, domain: str, tier: str, *, passed: bool) -> dict:
    return {
        "question_id": qid,
        "domain": domain,
        "tier": tier,
        "natural_language_question": f"question {qid}?",
        "expected_answer": "72",
        "actual_answer": "72" if passed else "13",
        "exact_match_result": "pass" if passed else "fail",
        "exact_match_detail": "",
        "platform_generated_sql": "SELECT 72",
        "judge_scores": {
            "factual_correctness": 5 if passed else 2,
            "completeness": 5 if passed else 2,
            "format_adherence": 5 if passed else 2,
            "sql_plausibility": 5 if passed else 2,
        },
        "judge_rationales": {
            k: "r"
            for k in (
                "factual_correctness",
                "completeness",
                "format_adherence",
                "sql_plausibility",
            )
        },
        "judge_overall": 5.0 if passed else 2.0,
        "prompt_version": "p",
        "model_version": "m",
    }


@pytest.fixture()
def runs(tmp_path: Path, monkeypatch):
    """A repo root with judge runs on disk, and an isolated baseline store."""

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("scorecard.baseline.BASELINE_DIR", tmp_path / "baselines")

    def write(
        domain: str,
        run_id: str,
        *,
        passes: int = 3,
        fails: int = 1,
        platform_version: str = "pulse-2026.09",
        dataset_version: str | None = None,
        pulse_mode: str = "live",
        judge: str = "llm",
        calibrated: bool = True,
        comparison_is_default: bool = True,
        row_domain: str | None = None,
    ) -> Path:
        directory = tmp_path / "release" / domain / "eval-runs" / run_id
        directory.mkdir(parents=True, exist_ok=True)
        rows = [
            _row(f"{domain}-{i}", row_domain or domain, "T1", passed=True)
            for i in range(passes)
        ] + [
            _row(f"{domain}-f{i}", row_domain or domain, "T1", passed=False)
            for i in range(fails)
        ]
        (directory / "results.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "run_timestamp_iso": "2026-09-23T12:00:00+00:00",
                    "platform_version": platform_version,
                    "dataset_version": (
                        dataset_version
                        if dataset_version is not None
                        else f"{domain}-dataset-v1"
                    ),
                    "scorecard_mode": "PREVIEW",
                    "judge": judge,
                    "mode": "combined",
                    "pulse_mode": pulse_mode,
                    "summary": {
                        "calibrated": calibrated,
                        "comparison_is_default": comparison_is_default,
                        "comparison_policy": "numeric=value labels=required",
                        "judge_temperature_enforced": True,
                        "judge_seed_enforced": True,
                    },
                    "rows": rows,
                }
            ),
            encoding="utf-8",
        )
        return directory

    return write


# --- discovery ----------------------------------------------------------------


def test_the_most_recent_run_is_used_by_default(runs, tmp_path: Path) -> None:
    runs("crm", "20260101T000000Z")
    runs("crm", "20260923T120000Z")

    (picked,) = collect(["crm"])

    assert picked.run_id == "20260923T120000Z"


def test_a_run_can_be_pinned(runs) -> None:
    runs("crm", "20260101T000000Z")
    runs("crm", "20260923T120000Z")

    (picked,) = collect(["crm"], {"crm": "20260101T000000Z"})

    assert picked.run_id == "20260101T000000Z"


def test_a_domain_with_no_runs_names_the_command_that_makes_one(runs) -> None:
    with pytest.raises(CombineError) as excinfo:
        collect(["sales"])

    assert "judge --domain sales" in str(excinfo.value)


def test_only_directories_with_results_count(runs, tmp_path: Path) -> None:
    runs("crm", "20260923T120000Z")
    # A run that died before writing results.json must not be picked as newest.
    (tmp_path / "release" / "crm" / "eval-runs" / "20260924T999999Z").mkdir(
        parents=True
    )

    assert mod.discover_run_ids("crm") == ["20260923T120000Z"]


# --- combining ----------------------------------------------------------------


def test_several_domains_land_in_one_scorecard(runs, tmp_path: Path) -> None:
    inputs = [
        load_run("crm", runs("crm", "r1", passes=3, fails=1).name),
        load_run("sales", runs("sales", "r1", passes=1, fails=3).name),
    ]

    outcome = mod.combine(inputs, out_dir=tmp_path / "out")

    domains = {r["domain"] for r in outcome.rows}
    assert domains == {"crm", "sales"}
    roll_ups = {r["domain"]: r["exact_match_pct"] for r in outcome.rows if r["tier"] == "ALL"}
    assert roll_ups == {"crm": 75.0, "sales": 25.0}


def test_each_domain_keeps_its_own_dataset_version(runs, tmp_path: Path) -> None:
    """§11.1 reports dataset_version per row; the domains differ."""

    inputs = [
        load_run("crm", runs("crm", "r1", dataset_version="crm-v1").name),
        load_run("sales", runs("sales", "r1", dataset_version="sales-v9").name),
    ]

    outcome = mod.combine(inputs, out_dir=tmp_path / "out")

    versions = {r["domain"]: r["dataset_version"] for r in outcome.rows}
    assert versions == {"crm": "crm-v1", "sales": "sales-v9"}


def test_all_four_artefacts_are_written(runs, tmp_path: Path) -> None:
    inputs = [load_run("crm", runs("crm", "r1").name)]

    outcome = mod.combine(inputs, out_dir=tmp_path / "out")

    for name in (
        "scorecard_summary.csv",
        "question_results.csv",
        "scorecard.md",
        "scorecard.pdf",
        "inputs.json",
    ):
        assert (outcome.out_dir / name).is_file(), name


def test_the_inputs_manifest_makes_it_traceable(runs, tmp_path: Path) -> None:
    """A scorecard must point back at the evidence it was built from."""

    inputs = [
        load_run("crm", runs("crm", "rA").name),
        load_run("sales", runs("sales", "rB").name),
    ]

    outcome = mod.combine(inputs, out_dir=tmp_path / "out")
    manifest = json.loads((outcome.out_dir / "inputs.json").read_text())

    assert [i["domain"] for i in manifest["inputs"]] == ["crm", "sales"]
    assert [i["run_id"] for i in manifest["inputs"]] == ["rA", "rB"]
    assert all(i["results_json"].endswith("results.json") for i in manifest["inputs"])


def test_the_provenance_note_names_every_input(runs, tmp_path: Path) -> None:
    inputs = [
        load_run("crm", runs("crm", "rA").name),
        load_run("sales", runs("sales", "rB").name),
    ]

    outcome = mod.combine(inputs, out_dir=tmp_path / "out")
    text = (outcome.out_dir / "scorecard.md").read_text(encoding="utf-8")

    assert "crm/rA" in text and "sales/rB" in text


def test_inputs_are_never_modified(runs, tmp_path: Path) -> None:
    """A run directory is evidence."""

    directory = runs("crm", "r1")
    before = (directory / "results.json").read_bytes()

    mod.combine([load_run("crm", "r1")], out_dir=tmp_path / "out")

    assert (directory / "results.json").read_bytes() == before


# --- the release gate ---------------------------------------------------------


def test_a_clean_set_has_no_blockers(runs) -> None:
    inputs = [load_run("crm", runs("crm", "r1").name),
              load_run("sales", runs("sales", "r1").name)]

    assert release_blockers(inputs) == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"calibrated": False}, "uncalibrated"),
        ({"judge": "heuristic"}, "heuristic test double"),
        ({"pulse_mode": "sql"}, "HC-4"),
        ({"platform_version": ""}, "no platform_version"),
        ({"dataset_version": ""}, "no dataset_version"),
        ({"comparison_is_default": False}, "relaxed the exact-match comparison"),
    ],
)
def test_each_release_condition_blocks_with_a_reason(runs, kwargs, expected) -> None:
    inputs = [load_run("crm", runs("crm", "r1", **kwargs).name)]

    blockers = release_blockers(inputs)

    assert any(expected in b for b in blockers), blockers


def test_mixed_platform_versions_block(runs) -> None:
    """A combined scorecard must describe one platform state."""

    inputs = [
        load_run("crm", runs("crm", "r1", platform_version="pulse-A").name),
        load_run("sales", runs("sales", "r1", platform_version="pulse-B").name),
    ]

    assert any("different platform versions" in b for b in release_blockers(inputs))


def test_the_same_domain_twice_blocks(runs) -> None:
    """Otherwise a domain is counted twice in its own baseline."""

    inputs = [
        load_run("crm", runs("crm", "r1").name),
        load_run("crm", runs("crm", "r2").name),
    ]

    assert any("counted twice" in b for b in release_blockers(inputs))


def test_rows_belonging_to_another_domain_block(runs) -> None:
    inputs = [load_run("crm", runs("crm", "r1", row_domain="sales").name)]

    assert any("contains rows for sales" in b for b in release_blockers(inputs))


def test_a_blocked_release_downgrades_to_preview_and_writes_nothing_immutable(
    runs, tmp_path: Path
) -> None:
    inputs = [load_run("crm", runs("crm", "r1", calibrated=False).name)]

    outcome = mod.combine(inputs, release=True, out_dir=tmp_path / "out")

    assert outcome.mode == "PREVIEW"
    assert outcome.blockers
    assert outcome.baseline_established is None
    assert not (tmp_path / "baselines").exists()


# --- the baseline, which only this step may touch ------------------------------


def test_a_clean_release_establishes_the_baseline(runs, tmp_path: Path) -> None:
    inputs = [
        load_run("crm", runs("crm", "r1", passes=3, fails=1).name),
        load_run("sales", runs("sales", "r1", passes=2, fails=2).name),
    ]

    outcome = mod.combine(inputs, release=True, out_dir=tmp_path / "out")

    assert outcome.mode == "RELEASE"
    assert outcome.baseline_established is not None
    recorded = json.loads(Path(outcome.baseline_established).read_text())
    # §14.1: one baseline covering every domain — the thing per-domain runs
    # could never produce.
    assert set(recorded["domains"]) == {"crm", "sales"}


def test_a_preview_run_never_establishes_a_baseline(runs, tmp_path: Path) -> None:
    inputs = [load_run("crm", runs("crm", "r1").name)]

    outcome = mod.combine(inputs, release=False, out_dir=tmp_path / "out")

    assert outcome.mode == "PREVIEW"
    assert outcome.baseline_established is None
    assert not (tmp_path / "baselines").exists()


def test_a_second_release_compares_and_flags_a_regression(
    runs, tmp_path: Path
) -> None:
    """The whole point of the baseline: catch a drop of 5 pp or more."""

    first = [load_run("crm", runs("crm", "r1", passes=4, fails=0).name)]  # 100%
    mod.combine(first, release=True, out_dir=tmp_path / "out1")

    second = [load_run("crm", runs("crm", "r2", passes=1, fails=3).name)]  # 25%
    outcome = mod.combine(second, release=True, out_dir=tmp_path / "out2")

    assert outcome.baseline_established is None  # immutable, not rewritten
    assert outcome.regressions == ["crm"]


def test_a_second_release_without_a_drop_flags_nothing(runs, tmp_path: Path) -> None:
    first = [load_run("crm", runs("crm", "r1", passes=3, fails=1).name)]  # 75%
    mod.combine(first, release=True, out_dir=tmp_path / "out1")

    second = [load_run("crm", runs("crm", "r2", passes=3, fails=1).name)]  # 75%
    outcome = mod.combine(second, release=True, out_dir=tmp_path / "out2")

    assert outcome.regressions == []


# --- exit codes ---------------------------------------------------------------


def test_exit_codes(runs, tmp_path: Path, monkeypatch) -> None:
    from scorecard.combine import build_argparser, run_from_args

    runs("crm", "r1", passes=4, fails=0)

    def _args(*argv):
        return build_argparser().parse_args(list(argv))

    # clean preview
    assert run_from_args(_args("--domains", "crm", "--out", str(tmp_path / "a"))) == 0
    # clean release establishes -> 0
    assert (
        run_from_args(_args("--domains", "crm", "--release", "--out", str(tmp_path / "b")))
        == 0
    )
    # a regression against that baseline -> 4
    runs("crm", "r2", passes=1, fails=3)
    assert (
        run_from_args(_args("--domains", "crm", "--release", "--out", str(tmp_path / "c")))
        == 4
    )


def test_a_blocked_release_exits_3(runs, tmp_path: Path) -> None:
    from scorecard.combine import build_argparser, run_from_args

    runs("crm", "r1", calibrated=False)
    args = build_argparser().parse_args(
        ["--domains", "crm", "--release", "--out", str(tmp_path / "out")]
    )

    assert run_from_args(args) == 3


def test_a_malformed_run_pin_exits_2(runs, tmp_path: Path) -> None:
    from scorecard.combine import build_argparser, run_from_args

    args = build_argparser().parse_args(["--run", "crm-no-equals"])

    assert run_from_args(args) == 2


def test_shared_version_reports_one_agreed_value() -> None:
    assert mod._shared_version({"dataset-v1.0.0"}) == "dataset-v1.0.0"


def test_shared_version_points_at_per_domain_rows_when_runs_disagree() -> None:
    shared = mod._shared_version({"dataset-v1.0.0", "dataset-v2.0.0"})
    assert shared == "multiple — see per-domain rows"


def test_shared_version_is_absent_when_no_run_recorded_one() -> None:
    """Empty is not 'multiple'.

    A reader who sees "multiple — see per-domain rows" goes looking for several
    versions. If no run captured one, that sends them after data that does not
    exist and dresses missing provenance up as rich provenance.
    """

    assert mod._shared_version(set()) == ""
