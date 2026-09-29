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
) -> None:
    calls: list[tuple[str, str, str, list[str] | None]] = []

    def record(name: str):
        def handler(args: argparse.Namespace) -> None:
            calls.append(
                (
                    name,
                    args.domain,
                    args.profile,
                    getattr(args, "command_argv", None),
                )
            )

        return handler

    monkeypatch.setattr(main_module, "run_build_dataset", record("build-dataset"))
    monkeypatch.setattr(main_module, "run_qa_build", record("qa-build"))
    monkeypatch.setattr(main_module, "run_judge", record("judge"))

    args = build_parser().parse_args(
        ["run-pipeline", "--domain", "crm", "--profile", "dev"]
    )
    run_pipeline(args)

    assert calls == [
        ("build-dataset", "crm", "dev", None),
        ("qa-build", "crm", "dev", None),
        (
            "judge",
            "crm",
            "dev",
            ["--allow-uncalibrated", "--limit", "3"],
        ),
    ]
