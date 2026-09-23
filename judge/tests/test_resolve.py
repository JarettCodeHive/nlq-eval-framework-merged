"""`judge` derives its inputs from --domain and --profile, like every other stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge.cli import build_argparser
from judge.resolve import (
    ResolutionError,
    apply_resolved_defaults,
    dataset_csv_dir,
    dataset_version_label,
    judge_input_csv,
    qa_release_dir,
)

CONTRACT_HEADER = (
    "natural_language_question,expected_answer,reference_sql,reference_tables,"
    "reference_fields,judge_reference,derivation_rationale\n"
)
CONTRACT_ROW = "How many accounts?,72,SELECT 72,accounts,id,There are 72.,count\n"
COMPANION_HEADER = (
    "question_id,tier,family,scoring_mode,natural_language_question\n"
)
COMPANION_ROW = "CRM-T1-01-01,T1,count,exact,How many accounts?\n"


def _repo(tmp_path: Path, *, qa_version: str = "0.3.0") -> Path:
    """A repo skeleton holding only the config the resolver reads."""

    qa_config = tmp_path / "qa_pairs" / "generator"
    qa_config.mkdir(parents=True)
    (qa_config / "config.json").write_text(
        json.dumps(
            {
                "domain": "crm",
                "qa_release": {
                    "version": qa_version,
                    "profile_outputs": {
                        "dev": "tmp/generated/{domain}/dev/qa_pairs",
                        "full": "release/{domain}/qa-pairs-v{qa_version}",
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
                "dataset_version": "dataset-v1.0.0",
                "output_paths": {
                    "dev": "tmp/generated/crm/dev",
                    "full": "release/crm/dataset-v1.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _qa_package(repo: Path, version: str, *, joined: bool = False) -> Path:
    directory = repo / "release" / "crm" / f"qa-pairs-v{version}"
    directory.mkdir(parents=True)
    (directory / "crm_qa_pairs.csv").write_text(
        CONTRACT_HEADER + CONTRACT_ROW, encoding="utf-8"
    )
    (directory / "crm_qa_pairs_companion.csv").write_text(
        COMPANION_HEADER + COMPANION_ROW, encoding="utf-8"
    )
    if joined:
        (directory / "crm_judge_input.csv").write_text("question_id\n", encoding="utf-8")
    return directory


def test_full_profile_resolves_the_configured_qa_release(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    expected = _qa_package(repo, "0.3.0")

    assert qa_release_dir("crm", "full", repo_root=repo) == expected


def test_a_newer_package_than_the_config_knows_about_still_resolves(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, qa_version="0.3.0")
    _qa_package(repo, "0.9.0")
    newest = _qa_package(repo, "0.10.0")
    # The config's own version has no package, so the filesystem decides — and
    # picks 0.10.0, not the 0.9.0 a string sort would call newest.
    assert qa_release_dir("crm", "full", repo_root=repo) == newest


def test_an_undeclared_domain_falls_back_to_the_release_layout(tmp_path: Path) -> None:
    """The Q&A config declares ONE domain's version; the rest share its layout."""

    repo = _repo(tmp_path, qa_version="0.3.0")
    sales = repo / "release" / "sales" / "qa-pairs-v1.2.0"
    sales.mkdir(parents=True)
    (sales / "sales_qa_pairs.csv").write_text(
        CONTRACT_HEADER + CONTRACT_ROW, encoding="utf-8"
    )

    assert qa_release_dir("sales", "full", repo_root=repo) == sales


def test_a_half_created_package_does_not_mask_a_complete_one(tmp_path: Path) -> None:
    repo = _repo(tmp_path, qa_version="0.9.9")  # config points somewhere empty
    complete = _qa_package(repo, "0.3.0")
    (repo / "release" / "crm" / "qa-pairs-v0.4.0").mkdir(parents=True)

    assert qa_release_dir("crm", "full", repo_root=repo) == complete


def test_dev_profile_resolves_the_disposable_package(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert qa_release_dir("crm", "dev", repo_root=repo) == (
        repo / "tmp" / "generated" / "crm" / "dev" / "qa_pairs"
    )


def test_the_judge_input_is_joined_on_demand_when_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    package = _qa_package(repo, "0.3.0")

    resolved = judge_input_csv("crm", "full", repo_root=repo)

    assert resolved == package / "crm_judge_input.csv"
    assert "CRM-T1-01-01" in resolved.read_text(encoding="utf-8")


def test_an_existing_judge_input_is_never_rebuilt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    package = _qa_package(repo, "0.3.0", joined=True)

    resolved = judge_input_csv("crm", "full", repo_root=repo)

    assert resolved.read_text(encoding="utf-8") == "question_id\n"
    assert resolved == package / "crm_judge_input.csv"


def test_a_missing_package_names_the_command_that_creates_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(ResolutionError) as excinfo:
        judge_input_csv("crm", "full", repo_root=repo)

    assert "qa-build --domain crm --profile full" in str(excinfo.value)


def test_dataset_version_label_carries_both_versions(tmp_path: Path) -> None:
    """Q&A templates and the dataset version move independently (§11.1)."""

    repo = _repo(tmp_path)
    _qa_package(repo, "0.3.0")

    assert (
        dataset_version_label("crm", "full", repo_root=repo)
        == "dataset-v1.0.0+qa-pairs-v0.3.0"
    )
    assert dataset_version_label("crm", "dev", repo_root=repo) == "dataset-v1.0.0+dev"


def test_dev_pulse_data_points_at_the_imperfect_stage(tmp_path: Path) -> None:
    """The pairs were authored against imperfect data, so that is what replays."""

    repo = _repo(tmp_path)
    (repo / "tmp" / "generated" / "crm" / "dev" / "imperfect").mkdir(parents=True)

    assert dataset_csv_dir("crm", "dev", repo_root=repo) == (
        repo / "tmp" / "generated" / "crm" / "dev" / "imperfect"
    )
    assert dataset_csv_dir("crm", "full", repo_root=repo) == (
        repo / "release" / "crm" / "dataset-v1.0.0"
    )


def test_two_flags_are_enough_for_a_run(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    package = _qa_package(repo, "0.3.0", joined=True)
    (repo / "release" / "crm" / "dataset-v1.0.0").mkdir(parents=True)
    monkeypatch.setenv("PLATFORM_VERSION", "pulse-2026.09")

    args = build_argparser().parse_args(
        ["--domain", "crm", "--profile", "full", "--pulse", "sql"]
    )
    resolved = apply_resolved_defaults(args, repo_root=repo)

    assert args.input_csv == str(package / "crm_judge_input.csv")
    assert args.pulse_data == str(repo / "release" / "crm" / "dataset-v1.0.0")
    assert args.dataset_version == "dataset-v1.0.0+qa-pairs-v0.3.0"
    assert args.platform_version == "pulse-2026.09"
    assert set(resolved.derived) == {
        "--input-csv",
        "--pulse-data",
        "--dataset-version",
        "--platform-version",
    }


def test_explicit_flags_are_never_overridden(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _qa_package(repo, "0.3.0", joined=True)

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
    _qa_package(repo, "0.3.0", joined=True)

    args = build_argparser().parse_args(
        ["--domain", "crm", "--profile", "full", "--pulse", "sql"]
    )
    with pytest.raises(ResolutionError) as excinfo:
        apply_resolved_defaults(args, repo_root=repo)

    assert "build-dataset --domain crm --profile full" in str(excinfo.value)
