from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from generators.core.base import PROJECT_ROOT
from generators.core.manifest import compute_sha256
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.generator import CRMBaseEntityGenerator
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.manifest import CRMManifestGenerator


BASELINE_PATH = (
    PROJECT_ROOT / "CRM_Config_Simplification_Pre_Migration_Baseline.json"
)
CONTROLLED_MANIFEST_SHA256 = (
    "8f85779c3ab5182032fb890d21a59c3b8071f56cd289340e62d994b511a10cc1"
)
FIXED_LIBRARY_VERSIONS = {
    "duckdb": "baseline",
    "faker": "baseline",
    "numpy": "baseline",
    "pandas": "baseline",
    "python": "baseline",
}


def test_assembled_crm_config_matches_pre_migration_baseline() -> None:
    baseline = _load_baseline()

    current_config = load_crm_config()

    assert current_config == baseline["assembled_config"]
    assert _canonical_sha256(current_config) == baseline["assembled_config_sha256"]


@pytest.mark.parametrize("profile", ["dev", "full"])
def test_profile_settings_match_pre_migration_baseline(profile: str) -> None:
    baseline = _load_baseline()

    assert settings_for_profile(profile).metadata() == baseline["profile_settings"][
        profile
    ]


def test_dev_stage_csvs_match_pre_migration_hashes(tmp_path: Path) -> None:
    baseline = _load_baseline()["dev_stages"]
    stage_tables = {
        "base": CRMBaseEntityGenerator.for_profile("dev").generate_tables(),
        "distributed": CRMDistributionApplier.for_profile(
            "dev"
        ).generate_distributed_tables(),
        "imperfect": CRMImperfectionInjector.for_profile(
            "dev"
        ).generate_imperfect_tables(),
    }

    for stage_name, tables in stage_tables.items():
        stage_path = tmp_path / stage_name
        stage_path.mkdir()
        for table_name, table in tables.items():
            csv_path = stage_path / f"{table_name}.csv"
            table.to_csv(csv_path, index=False, lineterminator="\n")
            file_hash = compute_sha256(csv_path)
            expected = baseline[stage_name][csv_path.name]

            assert table.columns.tolist() == expected["columns"]
            assert len(table) == expected["rows"]
            assert file_hash.bytes == expected["bytes"]
            assert file_hash.sha256 == expected["sha256"]


def test_full_release_baseline_matches_recorded_manifest_hashes() -> None:
    baseline = _load_baseline()
    manifest = baseline["normalized_manifest"]

    assert _pretty_sha256(manifest) == baseline["normalized_manifest_sha256"]
    assert {
        table_name: baseline["full_release"][f"{table_name}.csv"]["sha256"]
        for table_name in manifest["table_order"]
    } == manifest["hashes"]


def test_controlled_manifest_matches_pre_migration_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = CRMManifestGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    generator.generator.settings = generator.settings
    crm_config = load_crm_config()

    for table_name in generator.settings.table_order:
        columns = [
            field["name"] for field in crm_config["tables"][table_name]["fields"]
        ]
        (tmp_path / f"{table_name}.csv").write_text(
            ",".join(columns) + "\n" + ",".join("1" for _ in columns) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {
            "overall_passed": True,
            "gates": [
                {
                    "name": "row_caps",
                    "passed": True,
                    "checks": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "generators.crm.manifest._library_versions",
        lambda: FIXED_LIBRARY_VERSIONS,
    )
    monkeypatch.setattr(
        "generators.crm.manifest._relative",
        _stable_release_path(tmp_path),
    )

    manifest = generator.generate_manifest()

    assert _pretty_sha256(manifest) == CONTROLLED_MANIFEST_SHA256


def _load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _stable_release_path(output_path: Path) -> Any:
    configured_output = "release/crm/dataset-v1.0.0"

    def relative(path: Path) -> str:
        if path == output_path:
            return configured_output
        if path.parent == output_path:
            return f"{configured_output}/{path.name}"
        return str(path.relative_to(PROJECT_ROOT))

    return relative


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _pretty_sha256(value: Any) -> str:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return sha256(payload).hexdigest()
