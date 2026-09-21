"""Protect CRM/core behavior while Sales generation is implemented."""

from __future__ import annotations

import json

from generators.core.base import PROJECT_ROOT
from generators.core.manifest import compute_sha256
from main import COMMANDS, build_parser


BASELINE_PATH = PROJECT_ROOT / "Sales_Pre_Implementation_CRM_Core_Baseline.json"


def test_dataset_cli_contract_matches_sales_preimplementation_baseline() -> None:
    baseline = _load_baseline()["cli"]
    parser = build_parser()
    current_defaults = {
        command: vars(parser.parse_args([command]))
        for command in sorted(COMMANDS)
        if not command.startswith("qa-")
    }

    baseline_defaults = {
        command: details["defaults"]
        for command, details in baseline["commands"].items()
    }
    assert set(baseline_defaults).issubset(current_defaults)
    assert {
        command: current_defaults[command] for command in baseline_defaults
    } == baseline_defaults
    assert current_defaults["build-dataset"] == {
        "command": "build-dataset",
        "domain": "crm",
        "profile": "dev",
        "write_preview": False,
        "generated": False,
        "exported": False,
    }
    assert baseline["default_domain"] == "crm"
    assert baseline["default_profile"] == "dev"


def test_source_contracts_match_sales_preimplementation_baseline() -> None:
    baseline = _load_baseline()["source_contracts"]

    current = {
        relative_path: compute_sha256(PROJECT_ROOT / relative_path).sha256
        for relative_path in baseline
    }

    assert current == baseline


def test_new_dev_hashes_match_existing_locked_crm_baseline() -> None:
    baseline = _load_baseline()["dev_stages"]
    locked = json.loads(
        (
            PROJECT_ROOT / "CRM_Config_Simplification_Pre_Migration_Baseline.json"
        ).read_text(encoding="utf-8")
    )["dev_stages"]

    assert baseline == locked


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
