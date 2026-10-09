"""`judge` derives its inputs from --domain and --profile, like every other stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qa_pairs.utils.release_bundle import LegacyReleaseLayoutWarning
from qa_pairs.utils.release_bundle import use_release_version

from judge.cli import build_argparser
from judge.resolve import (
    ResolutionError,
    apply_resolved_defaults,
    dataset_csv_dir,
    dataset_version_label,
    judge_input_csv,
    qa_release_dir,
)

# Mirrors the real package: `question_id` and `tier` are in the contract file,
# which is what lets the judge read it directly instead of joining the companion.
CONTRACT_HEADER = (
    "question_id,tier,natural_language_question,expected_answer,reference_sql,"
    "reference_tables,reference_fields,judge_reference,derivation_rationale\n"
)
CONTRACT_ROW = (
    "CRM-T1-01-01,T1,How many accounts?,72,SELECT 72,accounts,id,"
    "There are 72.,count\n"
)
COMPANION_HEADER = "question_id,tier,family,scoring_mode,natural_language_question\n"
COMPANION_ROW = "CRM-T1-01-01,T1,count,exact,How many accounts?\n"


def _repo(tmp_path: Path, *, release_version: str = "v1.0.0") -> Path:
    """A repo skeleton holding only the config the resolver reads."""

    qa_config = tmp_path / "qa_pairs" / "generator" / "crm"
    qa_config.mkdir(parents=True)
    (qa_config / "config.json").write_text(
        json.dumps(
            {
                "domain": "crm",
                "dataset": {
                    "release_config_path": "config/generation/crm/release.json"
                },
                "qa_release": {
                    "profile_outputs": {
                        "dev": "tmp/generated/{domain}/dev/qa_pairs",
                        "full": "release/{release_version}/{domain}/qa_pairs",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    release_config = tmp_path / "config" / "generation" / "crm"
    release_config.mkdir(parents=True)
    (release_config / "release.json").write_text(
        json.dumps(
            {
                "release_version": release_version,
                "dataset_version": release_version,
                "output_paths": {
                    "dev": "tmp/generated/crm/dev",
                    "full": "release/{release_version}/crm/dataset",
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _qa_package(repo: Path, version: str) -> Path:
    directory = repo / "release" / version / "crm" / "qa_pairs"
    directory.mkdir(parents=True)
    (directory / "crm_qa_pairs.csv").write_text(
        CONTRACT_HEADER + CONTRACT_ROW, encoding="utf-8"
    )
    (directory / "crm_qa_pairs_companion.csv").write_text(
        COMPANION_HEADER + COMPANION_ROW, encoding="utf-8"
    )
    return directory


def test_full_profile_resolves_the_configured_qa_release(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    expected = _qa_package(repo, "v1.0.0")

    assert qa_release_dir("crm", "full", repo_root=repo) == expected


def test_full_profile_reads_legacy_package_with_warning(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    legacy = repo / "release" / "crm" / "v1.0.0" / "qa_pairs"
    legacy.mkdir(parents=True)

    with pytest.warns(LegacyReleaseLayoutWarning):
        resolved = qa_release_dir("crm", "full", repo_root=repo)

    assert resolved == legacy


def test_a_cli_selected_release_version_overrides_the_configured_default(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    selected = _qa_package(repo, "client-2.4")
    with use_release_version("client-2.4"):
        assert qa_release_dir("crm", "full", repo_root=repo) == selected


def test_an_empty_selected_bundle_does_not_fall_back_to_another_version(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _qa_package(repo, "v1.0.0")
    with use_release_version("v2.0.0"):
        assert qa_release_dir("crm", "full", repo_root=repo) == (
            repo / "release" / "v2.0.0" / "crm" / "qa_pairs"
        )


def test_dev_profile_resolves_the_disposable_package(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert qa_release_dir("crm", "dev", repo_root=repo) == (
        repo / "tmp" / "generated" / "crm" / "dev" / "qa_pairs"
    )


def test_the_judge_scores_the_qa_authors_own_file(tmp_path: Path) -> None:
    """There is no derived input CSV any more, and nothing is written.

    A joined copy used to be built here, carrying `family` and `scoring_mode`
    from the companion. `InputRow` has neither field, so the loader dropped them
    again on the way in and the copy earned nothing. What it cost was a file in
    the Q&A package directory that `qa-build` owns and wipes, which could then
    go stale behind a corrected pair.
    """

    repo = _repo(tmp_path)
    package = _qa_package(repo, "v1.0.0")

    resolved = judge_input_csv("crm", "full", repo_root=repo)

    assert resolved == package / "crm_qa_pairs.csv"
    assert "CRM-T1-01-01" in resolved.read_text(encoding="utf-8")


def test_resolving_the_input_writes_nothing(tmp_path: Path) -> None:
    """Resolution is a lookup. A second call must not have changed the tree."""

    repo = _repo(tmp_path)
    package = _qa_package(repo, "v1.0.0")
    before = sorted(p.name for p in package.iterdir())

    judge_input_csv("crm", "full", repo_root=repo)
    judge_input_csv("crm", "full", repo_root=repo)

    assert sorted(p.name for p in package.iterdir()) == before
    assert not (package / "crm_judge_input.csv").exists()


def test_a_missing_package_names_the_command_that_creates_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(ResolutionError) as excinfo:
        judge_input_csv("crm", "full", repo_root=repo)

    assert "qa-build --domain crm --profile full" in str(excinfo.value)


def test_dataset_version_label_is_the_unified_release_version(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    _qa_package(repo, "v1.0.0")

    assert dataset_version_label("crm", "full", repo_root=repo) == "v1.0.0"
    assert dataset_version_label("crm", "dev", repo_root=repo) == "v1.0.0+dev"


def test_dev_pulse_data_points_at_the_imperfect_stage(tmp_path: Path) -> None:
    """The pairs were authored against imperfect data, so that is what replays."""

    repo = _repo(tmp_path)
    (repo / "tmp" / "generated" / "crm" / "dev" / "imperfect").mkdir(parents=True)

    assert dataset_csv_dir("crm", "dev", repo_root=repo) == (
        repo / "tmp" / "generated" / "crm" / "dev" / "imperfect"
    )
    assert dataset_csv_dir("crm", "full", repo_root=repo) == (
        repo / "release" / "v1.0.0" / "crm" / "dataset"
    )


def test_two_flags_are_enough_for_a_run(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    package = _qa_package(repo, "v1.0.0")
    (repo / "release" / "v1.0.0" / "crm" / "dataset").mkdir(parents=True)
    monkeypatch.setenv("PLATFORM_VERSION", "pulse-2026.09")

    args = build_argparser().parse_args(
        ["--domain", "crm", "--profile", "full", "--pulse", "sql"]
    )
    resolved = apply_resolved_defaults(args, repo_root=repo)

    assert args.input_csv == str(package / "crm_qa_pairs.csv")
    assert args.pulse_data == str(repo / "release" / "v1.0.0" / "crm" / "dataset")
    assert args.dataset_version == "v1.0.0"
    assert args.platform_version == "pulse-2026.09"
    assert set(resolved.derived) == {
        "--input-csv",
        "--pulse-data",
        "--dataset-version",
        "--platform-version",
    }


def test_explicit_flags_are_never_overridden(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _qa_package(repo, "v1.0.0")

    args = build_argparser().parse_args(
        [
            "--domain",
            "crm",
            "--profile",
            "full",
            "--input-csv",
            "/elsewhere/pairs.csv",
            "--dataset-version",
            "hand-picked",
            "--platform-version",
            "v9",
        ]
    )
    resolved = apply_resolved_defaults(args, repo_root=repo)

    assert args.input_csv == "/elsewhere/pairs.csv"
    assert args.dataset_version == "hand-picked"
    assert args.platform_version == "v9"
    assert resolved.derived == {}


def test_sql_pulse_refuses_to_guess_when_the_dataset_is_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _qa_package(repo, "v1.0.0")

    args = build_argparser().parse_args(
        ["--domain", "crm", "--profile", "full", "--pulse", "sql"]
    )
    with pytest.raises(ResolutionError) as excinfo:
        apply_resolved_defaults(args, repo_root=repo)

    assert "build-dataset --domain crm --profile full" in str(excinfo.value)


def test_a_pair_file_without_question_id_is_refused(tmp_path: Path) -> None:
    """The retired join sourced `question_id` and `tier` from the COMPANION.

    Reading the contract directly is only safe while the contract carries them.
    `load_input_csv` treats both as auto-fillable and will synthesize `x-0001`
    and `"unknown"` from the filename — correct for an ad-hoc CSV behind
    `--input-csv`, catastrophic for a release package, because every question id
    in the run would be fabricated and per-tier reporting would be empty with no
    error anywhere.
    """

    repo = _repo(tmp_path)
    package = repo / "release" / "v1.0.0" / "crm" / "qa_pairs"
    package.mkdir(parents=True)
    (package / "crm_qa_pairs.csv").write_text(
        "natural_language_question,expected_answer,reference_sql,judge_reference\n"
        "How many accounts?,72,SELECT 72,There are 72.\n",
        encoding="utf-8",
    )

    with pytest.raises(ResolutionError) as excinfo:
        judge_input_csv("crm", "full", repo_root=repo)

    message = str(excinfo.value)
    assert "question_id" in message and "tier" in message
    assert "fabricated" in message
