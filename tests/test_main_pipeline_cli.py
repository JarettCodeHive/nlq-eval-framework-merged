"""Tests for the end-to-end root CLI wrapper."""

from __future__ import annotations

import argparse

import pytest

import main as main_module
from main import COMMANDS, build_parser, run_pipeline


def test_run_pipeline_is_registered_and_accepts_only_shared_inputs() -> None:
    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "sales", "--profile", "full"]
    )

    assert COMMANDS["run-pipeline"] is run_pipeline
    assert args.domain == "sales"
    assert args.profile == "full"


def test_run_pipeline_runs_each_command_in_order_with_judge_defaults(
    monkeypatch,
    capsys,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def record(name: str):
        def handler(args: argparse.Namespace) -> None:
            calls.append((name, vars(args).copy()))

        return handler

    monkeypatch.setattr(main_module, "run_build_dataset", record("build-dataset"))
    monkeypatch.setattr(main_module, "run_qa_build", record("qa-build"))
    monkeypatch.setattr(main_module, "run_dataset_delete", record("dataset-delete"))
    monkeypatch.setattr(main_module, "run_dataset_upload", record("dataset-upload"))
    monkeypatch.setattr(main_module, "run_judge", record("judge"))

    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "crm", "--profile", "dev"]
    )
    run_pipeline(args)

    assert calls == [
        ("build-dataset", {"domain": "crm", "profile": "dev"}),
        ("qa-build", {"domain": "crm", "profile": "dev"}),
        (
            "dataset-delete",
            {"domain": "crm", "profile": "dev", "yes": True, "dry_run": False},
        ),
        (
            "dataset-upload",
            {
                "domain": "crm",
                "profile": "dev",
                "replace": False,
                "dry_run": False,
            },
        ),
        (
            "judge",
            {
                "domain": "crm",
                "profile": "dev",
                "command_argv": ["--allow-uncalibrated", "--limit", "3"],
            },
        ),
    ]
    output = capsys.readouterr().out
    expected = [
        "[1/5] START  Build and validate dataset",
        "[1/5] DONE   Build and validate dataset",
        "[2/5] START  Build and verify Q&A pairs",
        "[2/5] DONE   Build and verify Q&A pairs",
        "[3/5] START  Delete existing platform dataset",
        "[3/5] DONE   Delete existing platform dataset",
        "[4/5] START  Upload and verify platform dataset",
        "[4/5] DONE   Upload and verify platform dataset",
        "[5/5] START  Run three-question judge preview",
        "[5/5] DONE   Run three-question judge preview",
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
    assert "[1/5] FAILED Build and validate dataset" in captured.err
    assert "FAILED domain=crm profile=dev" in captured.err
    assert "[2/5] START" not in captured.out
