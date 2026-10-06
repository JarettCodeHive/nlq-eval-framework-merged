from __future__ import annotations

import pytest

import main as main_module
from main import COMMANDS, build_parser
from main import run_qa_build


QA_COMMANDS = (
    "qa-build",
    "qa-stage-dataset",
    "qa-validate-dataset",
    "qa-author-fixtures",
    "qa-generate-pairs",
    "qa-generate-rephrases",
)


@pytest.mark.parametrize("command", QA_COMMANDS)
@pytest.mark.parametrize("profile", ("dev", "full"))
def test_qa_commands_accept_supported_profiles(command: str, profile: str) -> None:
    args = build_parser().parse_args([command, "--profile", profile])

    assert args.command == command
    assert args.profile == profile
    assert command in COMMANDS


@pytest.mark.parametrize("profile", ("dev", "full"))
def test_crm_qa_build_runs_complete_release_steps_in_order(
    profile: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setitem(
        main_module.QA_STAGE,
        "crm",
        lambda selected: calls.append(("stage", selected)),
    )
    monkeypatch.setitem(
        main_module.QA_VALIDATE,
        "crm",
        lambda selected: calls.append(("validate", selected)),
    )
    monkeypatch.setitem(
        main_module.QA_GENERATE,
        "crm",
        lambda selected: calls.append(("pairs", selected)),
    )
    monkeypatch.setattr(
        main_module,
        "generate_seed_fixtures",
        lambda selected: calls.append(("fixtures", selected)),
    )
    monkeypatch.setitem(
        main_module.QA_REPHRASE,
        "crm",
        lambda selected: calls.append(("rephrases", selected)),
    )
    args = build_parser().parse_args(
        ["qa-build", "--domain", "crm", "--profile", profile]
    )

    run_qa_build(args)

    assert calls == [
        ("stage", profile),
        ("validate", profile),
        ("pairs", profile),
        ("rephrases", profile),
    ]
    output = capsys.readouterr().out
    assert "Step 1/4:" in output
    assert "Step 2/4:" in output
    assert "Step 3/4:" in output
    assert "Step 4/4:" in output
    assert "crm Q&A pair build passed" in output


@pytest.mark.parametrize("domain", ("sales", "finance"))
def test_other_domain_qa_builds_run_three_release_steps(
    domain: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setitem(
        main_module.QA_STAGE,
        domain,
        lambda selected: calls.append(("stage", selected)),
    )
    monkeypatch.setitem(
        main_module.QA_VALIDATE,
        domain,
        lambda selected: calls.append(("validate", selected)),
    )
    monkeypatch.setitem(
        main_module.QA_GENERATE,
        domain,
        lambda selected: calls.append(("pairs", selected)),
    )
    args = build_parser().parse_args(["qa-build", "--domain", domain])

    run_qa_build(args)

    assert calls == [("stage", "dev"), ("validate", "dev"), ("pairs", "dev")]
