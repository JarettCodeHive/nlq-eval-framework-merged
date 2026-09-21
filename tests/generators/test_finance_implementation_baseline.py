"""Protect CRM, Sales, and core behavior during Finance implementation."""

from __future__ import annotations

import json

from generators.core.base import PROJECT_ROOT
from main import COMMANDS, build_parser
from scripts.capture_finance_implementation_baseline import (
    capture_current_release_references,
)
from scripts.capture_finance_implementation_baseline import capture_dev_stages
from scripts.capture_finance_implementation_baseline import current_source_contracts


BASELINE_PATH = PROJECT_ROOT / "Finance_Pre_Implementation_CRM_Sales_Core_Baseline.json"


def test_existing_dataset_cli_contract_matches_finance_baseline() -> None:
    """Keep existing commands and CRM/Sales parsing stable as Finance is added."""

    baseline = _load_baseline()["cli"]
    parser = build_parser()
    for command, expected in baseline["commands"].items():
        assert command in COMMANDS
        handler = COMMANDS[command]
        assert f"{handler.__module__}.{handler.__name__}" == expected["handler"]
        assert vars(parser.parse_args([command])) == expected["defaults"]
        for domain, expected_args in expected["existing_domains"].items():
            assert (
                vars(parser.parse_args([command, "--domain", domain])) == expected_args
            )

    assert baseline["default_domain"] == "crm"
    assert baseline["default_profile"] == "dev"


def test_crm_and_sales_source_contracts_match_finance_baseline() -> None:
    """Prevent accidental edits to accepted CRM and Sales contracts."""

    assert current_source_contracts() == _load_baseline()["source_contracts"]


def test_fresh_dev_hashes_match_finance_baseline(tmp_path) -> None:
    """Regenerate every CRM/Sales stage and compare byte-level CSV metadata."""

    assert capture_dev_stages(tmp_path) == _load_baseline()["dev_stages"]


def test_existing_full_release_hashes_match_finance_baseline() -> None:
    """Ensure Finance work does not mutate current CRM or Sales releases."""

    assert (
        capture_current_release_references()
        == _load_baseline()["full_release_references"]
    )


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
