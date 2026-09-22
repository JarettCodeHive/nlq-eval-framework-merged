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
def test_qa_build_runs_only_production_steps_in_order(
    profile: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_module,
        "stage_qa_dataset",
        lambda selected: calls.append(("stage", selected)),
    )
    monkeypatch.setattr(
        main_module,
        "validate_qa_dataset",
        lambda selected: calls.append(("validate", selected)),
    )
    monkeypatch.setattr(
        main_module,
        "generate_pairs",
        lambda selected: calls.append(("pairs", selected)),
    )
    monkeypatch.setattr(
        main_module,
        "generate_seed_fixtures",
        lambda selected: calls.append(("fixtures", selected)),
    )
    monkeypatch.setattr(
        main_module,
        "generate_rephrases",
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
    ]
    output = capsys.readouterr().out
    assert "Step 1/3:" in output
    assert "Step 2/3:" in output
    assert "Step 3/3:" in output
    assert "CRM Q&A pair build passed" in output


@pytest.mark.parametrize("domain", ("sales", "finance"))
def test_qa_build_remains_crm_only(domain: str) -> None:
    args = build_parser().parse_args(["qa-build", "--domain", domain])

    with pytest.raises(
        NotImplementedError,
        match="Q&A commands currently support only crm",
    ):
        run_qa_build(args)
