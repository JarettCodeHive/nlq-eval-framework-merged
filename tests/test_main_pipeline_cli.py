"""Tests for the end-to-end root CLI wrapper."""

from __future__ import annotations

import argparse

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
    assert capsys.readouterr().out.splitlines() == [
        "Pipeline step 1/5: build dataset",
        "Pipeline step 2/5: build Q&A pairs",
        "Pipeline step 3/5: delete platform dataset",
        "Pipeline step 4/5: upload platform dataset",
        "Pipeline step 5/5: judge",
    ]
