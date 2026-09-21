"""Dataset-domain dispatch tests for the root command runner."""

from __future__ import annotations

import argparse

import pytest

from generators.domain_registry import DATASET_DOMAINS
from generators.domain_registry import dataset_domain
from main import COMMANDS
from main import build_parser
from main import run_apply_distributions
from main import run_apply_imperfections
from main import run_build_dataset
from main import run_generate_base
from main import run_validate_config
from main import run_qa_generate_pairs


DATASET_COMMANDS = tuple(
    command for command in sorted(COMMANDS) if not command.startswith("qa-")
)


def test_domain_registry_exposes_implemented_domain_components() -> None:
    assert set(DATASET_DOMAINS) == {"crm", "sales", "finance"}

    for domain in ("crm", "sales", "finance"):
        runtime = dataset_domain(domain)
        component_types = (
            runtime.base_generator,
            runtime.distribution_applier,
            runtime.imperfection_injector,
            runtime.relational_validator,
            runtime.csv_exporter,
            runtime.row_cap_validator,
            runtime.fk_validator,
            runtime.join_path_validator,
            runtime.imperfection_rate_validator,
            runtime.hash_computer,
            runtime.data_dictionary_generator,
            runtime.schema_sql_generator,
            runtime.reproducibility_validator,
            runtime.manifest_generator,
        )
        assert all(
            component.__module__.startswith(f"generators.{domain}.")
            for component in component_types
        )


@pytest.mark.parametrize("domain", ("sales", "finance"))
@pytest.mark.parametrize("command", DATASET_COMMANDS)
def test_dataset_commands_accept_registered_domains(
    command: str,
    domain: str,
) -> None:
    args = build_parser().parse_args([command, "--domain", domain])

    assert args.command == command
    assert args.domain == domain
    assert args.profile == "dev"


def test_unknown_dataset_domain_has_clear_error() -> None:
    with pytest.raises(
        NotImplementedError,
        match="supported domains: crm, finance, sales",
    ):
        dataset_domain("logistics")


@pytest.mark.parametrize(
    ("domain", "pipeline_name", "display_name"),
    (
        ("crm", "CRMDatasetPipeline", "CRM"),
        ("finance", "FinanceDatasetPipeline", "Finance"),
        ("sales", "SalesDatasetPipeline", "Sales"),
    ),
)
def test_build_dataset_dispatches_selected_profile(
    domain: str,
    pipeline_name: str,
    display_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class _Pipeline:
        def run(self) -> str:
            calls.append("run")
            return f"tmp/generated/{domain}/dev/imperfect"

    def for_profile(profile: str, progress: object) -> _Pipeline:
        assert profile == "dev"
        assert progress is not None
        calls.append("for_profile")
        return _Pipeline()

    monkeypatch.setattr(
        f"main.{pipeline_name}.for_profile",
        for_profile,
    )

    run_build_dataset(_args("build-dataset", domain=domain))

    assert calls == ["for_profile", "run"]
    output = capsys.readouterr().out
    assert f"{display_name} dataset build passed" in output
    assert "profile: dev" in output
    assert f"tmp/generated/{domain}/dev/imperfect" in output


def test_sales_validate_config_runs_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_validate_config(_args("validate-config"))

    output = capsys.readouterr().out
    assert "Config/schema validation passed" in output
    assert "domain: sales" in output
    assert "quotations:" in output


def test_sales_generate_base_runs_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_generate_base(_args("generate-base"))

    output = capsys.readouterr().out
    assert "Generated clean Sales base entities" in output
    assert "deals_with_multiple_products:" in output
    assert "products_in_multiple_deals:" in output


def test_finance_validate_config_runs_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_validate_config(_args("validate-config", domain="finance"))

    output = capsys.readouterr().out
    assert "Config/schema validation passed" in output
    assert "domain: finance" in output
    assert "ledger_entries:" in output
    assert "fx_rates:" in output


def test_finance_generate_base_runs_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_generate_base(_args("generate-base", domain="finance"))

    output = capsys.readouterr().out
    assert "Generated clean Finance base entities" in output
    assert "unposted_transactions:" in output
    assert "transactions_with_multiple_accounts:" in output
    assert "transaction_fx_keys_covered: True" in output


def test_finance_distributions_run_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_apply_distributions(_args("apply-distributions", domain="finance"))

    output = capsys.readouterr().out
    assert "Applied Finance statistical distributions" in output
    assert "transaction_amount_mean:" in output
    assert "ledger_lines_per_posted_transaction_mean:" in output
    assert "populated_fx_rate_min:" in output


def test_finance_imperfections_run_through_root_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_apply_imperfections(_args("apply-imperfections", domain="finance"))

    output = capsys.readouterr().out
    assert "Injected Finance controlled imperfections" in output
    assert "budget_near_duplicates: 1" in output
    assert "fx_rates.rate_empty: 10" in output
    assert "transaction_boundary_dates: 4" in output


@pytest.mark.parametrize("domain", ("sales", "finance"))
def test_qa_commands_remain_crm_only(domain: str) -> None:
    with pytest.raises(
        NotImplementedError, match="Q&A commands currently support only crm"
    ):
        run_qa_generate_pairs(_args("qa-generate-pairs", domain=domain))


def _args(command: str, domain: str = "sales") -> argparse.Namespace:
    return argparse.Namespace(
        command=command,
        domain=domain,
        profile="dev",
        write_preview=False,
        generated=False,
        exported=False,
    )
