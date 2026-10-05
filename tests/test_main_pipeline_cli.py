"""Tests for the end-to-end root CLI wrapper."""

from __future__ import annotations

import argparse

import pytest

import main as main_module
from main import COMMANDS, build_parser, run_pipeline


def test_run_pipeline_is_registered_and_accepts_only_shared_inputs() -> None:
    args = build_parser().parse_args(
        [
            "run-pipeline",
            "--domain",
            "sales",
            "--profile",
            "full",
            "--version",
            "client-2.4.0",
        ]
    )

    assert COMMANDS["run-pipeline"] is run_pipeline
    assert args.domain == "sales"
    assert args.profile == "full"
    assert args.release_version == "client-2.4.0"
    assert args.keep_platform_data is False

    retained = build_parser().parse_args(
        ["run-pipeline", "--keep-platform-data"]
    )
    assert retained.keep_platform_data is True


def test_run_pipeline_runs_each_command_in_order_with_judge_defaults(
    monkeypatch,
    capsys,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    upload_outcome = object()

    def record(name: str):
        def handler(args: argparse.Namespace):
            calls.append((name, vars(args).copy()))
            if name == "dataset-upload":
                return upload_outcome

        return handler

    monkeypatch.setattr(main_module, "run_build_dataset", record("build-dataset"))
    monkeypatch.setattr(main_module, "run_qa_build", record("qa-build"))
    monkeypatch.setattr(main_module, "run_dataset_delete", record("dataset-delete"))
    monkeypatch.setattr(main_module, "run_dataset_upload", record("dataset-upload"))
    monkeypatch.setattr(main_module, "run_judge", record("judge"))
    monkeypatch.setattr(
        main_module, "run_uploaded_data_cleanup", record("platform-cleanup")
    )

    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "crm", "--profile", "dev"]
    )
    run_pipeline(args)

    assert calls == [
        (
            "build-dataset",
            {"domain": "crm", "profile": "dev", "release_version": None},
        ),
        ("qa-build", {"domain": "crm", "profile": "dev", "release_version": None}),
        (
            "dataset-delete",
            {
                "domain": "crm",
                "profile": "dev",
                "yes": True,
                "dry_run": False,
                "release_version": None,
            },
        ),
        (
            "dataset-upload",
            {
                "domain": "crm",
                "profile": "dev",
                "replace": False,
                "dry_run": False,
                "release_version": None,
                "cleanup_after_upload": True,
            },
        ),
        (
            "judge",
            {
                "domain": "crm",
                "profile": "dev",
                "command_argv": ["--allow-uncalibrated", "--limit", "3"],
                "release_version": None,
            },
        ),
        (
            "platform-cleanup",
            {
                "domain": "crm",
                "profile": "dev",
                "release_version": None,
                "upload_outcome": upload_outcome,
            },
        ),
    ]
    output = capsys.readouterr().out
    expected = [
        "[1/6] START  Build and validate dataset",
        "[1/6] DONE   Build and validate dataset",
        "[2/6] START  Build and verify Q&A pairs",
        "[2/6] DONE   Build and verify Q&A pairs",
        "[3/6] START  Delete existing platform dataset",
        "[3/6] DONE   Delete existing platform dataset",
        "[4/6] START  Upload and verify platform dataset",
        "[4/6] DONE   Upload and verify platform dataset",
        "[5/6] START  Run three-question judge preview",
        "[5/6] DONE   Run three-question judge preview",
        "[6/6] START  Delete uploaded platform data",
        "[6/6] DONE   Delete uploaded platform data",
        "SUCCESS domain=crm profile=dev",
    ]
    positions = [output.index(message) for message in expected]
    assert positions == sorted(positions)


def test_run_pipeline_reports_the_failed_stage_and_stops(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []

    def fail(_args: argparse.Namespace) -> None:
        calls.append("build-dataset")
        raise RuntimeError("generation failed")

    monkeypatch.setattr(main_module, "run_build_dataset", fail)
    monkeypatch.setattr(
        main_module,
        "run_qa_build",
        lambda _args: calls.append("qa-build"),
    )

    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "crm", "--profile", "dev"]
    )
    with pytest.raises(RuntimeError, match="generation failed"):
        run_pipeline(args)

    captured = capsys.readouterr()
    assert calls == ["build-dataset"]
    assert "[1/6] FAILED Build and validate dataset" in captured.err
    assert "FAILED domain=crm profile=dev" in captured.err
    assert "[2/6] START" not in captured.out


def test_keep_platform_data_skips_post_run_cleanup(monkeypatch, capsys) -> None:
    outcome = object()
    cleaned = []

    monkeypatch.setattr(main_module, "run_build_dataset", lambda _args: None)
    monkeypatch.setattr(main_module, "run_qa_build", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_delete", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_upload", lambda _args: outcome)
    monkeypatch.setattr(main_module, "run_judge", lambda _args: None)
    monkeypatch.setattr(
        main_module,
        "run_uploaded_data_cleanup",
        lambda _args: cleaned.append(True),
    )

    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "crm", "--keep-platform-data"]
    )
    run_pipeline(args)

    assert cleaned == []
    assert "cleanup skipped: --keep-platform-data" in capsys.readouterr().out


def test_judge_failure_still_cleans_uploaded_entities(monkeypatch) -> None:
    outcome = object()
    cleaned = []

    monkeypatch.setattr(main_module, "run_build_dataset", lambda _args: None)
    monkeypatch.setattr(main_module, "run_qa_build", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_delete", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_upload", lambda _args: outcome)
    monkeypatch.setattr(
        main_module,
        "run_judge",
        lambda _args: (_ for _ in ()).throw(RuntimeError("judge failed")),
    )
    monkeypatch.setattr(
        main_module,
        "run_uploaded_data_cleanup",
        lambda args: cleaned.append(args.upload_outcome),
    )

    with pytest.raises(RuntimeError, match="judge failed"):
        run_pipeline(build_parser().parse_args(["run-pipeline"]))

    assert cleaned == [outcome]


def test_partial_upload_failure_still_cleans_created_entities(monkeypatch) -> None:
    outcome = object()
    cleaned = []

    monkeypatch.setattr(main_module, "run_build_dataset", lambda _args: None)
    monkeypatch.setattr(main_module, "run_qa_build", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_delete", lambda _args: None)

    def fail_upload(args: argparse.Namespace) -> None:
        args.upload_outcome = outcome
        raise RuntimeError("upload failed")

    monkeypatch.setattr(main_module, "run_dataset_upload", fail_upload)
    monkeypatch.setattr(
        main_module,
        "run_uploaded_data_cleanup",
        lambda args: cleaned.append(args.upload_outcome),
    )

    with pytest.raises(RuntimeError, match="upload failed"):
        run_pipeline(build_parser().parse_args(["run-pipeline"]))

    assert cleaned == [outcome]


def test_cleanup_failure_fails_an_otherwise_successful_pipeline(monkeypatch) -> None:
    outcome = object()

    monkeypatch.setattr(main_module, "run_build_dataset", lambda _args: None)
    monkeypatch.setattr(main_module, "run_qa_build", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_delete", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_upload", lambda _args: outcome)
    monkeypatch.setattr(main_module, "run_judge", lambda _args: None)
    monkeypatch.setattr(
        main_module,
        "run_uploaded_data_cleanup",
        lambda _args: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        run_pipeline(build_parser().parse_args(["run-pipeline"]))


def test_primary_failure_is_preserved_when_cleanup_also_fails(monkeypatch) -> None:
    outcome = object()

    monkeypatch.setattr(main_module, "run_build_dataset", lambda _args: None)
    monkeypatch.setattr(main_module, "run_qa_build", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_delete", lambda _args: None)
    monkeypatch.setattr(main_module, "run_dataset_upload", lambda _args: outcome)
    monkeypatch.setattr(
        main_module,
        "run_judge",
        lambda _args: (_ for _ in ()).throw(RuntimeError("judge failed")),
    )
    monkeypatch.setattr(
        main_module,
        "run_uploaded_data_cleanup",
        lambda _args: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="judge failed") as excinfo:
        run_pipeline(build_parser().parse_args(["run-pipeline"]))

    assert any("cleanup failed" in note for note in excinfo.value.__notes__)
