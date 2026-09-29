from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.config import load_project_management_config
from generators.project_management.data_dictionary import FIELD_DESCRIPTIONS
from generators.project_management.data_dictionary import (
    ProjectManagementDataDictionaryGenerator,
)
from generators.project_management.data_dictionary import TABLE_DESCRIPTIONS
from generators.project_management.data_dictionary import _type_text


def test_dictionary_includes_every_configured_table_and_field() -> None:
    generator = ProjectManagementDataDictionaryGenerator.for_profile("full")
    config = load_project_management_config()
    markdown = generator.generate_markdown()
    expected_fields = {
        f"{table_name}.{field['name']}"
        for table_name in config["table_order"]
        for field in config["tables"][table_name]["fields"]
    }

    assert list(TABLE_DESCRIPTIONS) == config["table_order"]
    assert set(FIELD_DESCRIPTIONS) == expected_fields
    for table_name in config["table_order"]:
        assert f"### `{table_name}`" in markdown
        for field in config["tables"][table_name]["fields"]:
            assert f"| `{field['name']}` | `{_type_text(field)}` |" in markdown


def test_dictionary_documents_values_distributions_and_relationships() -> None:
    markdown = ProjectManagementDataDictionaryGenerator.for_profile(
        "full"
    ).generate_markdown()

    assert "## Allowed Domain Values" in markdown
    assert "`Planning`, `Active`, `OnHold`, `Completed`, `Cancelled`" in markdown
    assert "## Distribution Configuration" in markdown
    assert "`project_budget` | `pareto_amount`" in markdown
    assert "`time_entry_frequency` | `poisson_frequency`" in markdown
    assert "explicit Task-to-Resource many-to-many bridge" in markdown
    assert "semantic_composite_membership" in markdown
    assert "Project-to-Task LEFT JOIN" in markdown


def test_dictionary_documents_date_decimal_and_metric_semantics() -> None:
    markdown = ProjectManagementDataDictionaryGenerator.for_profile(
        "full"
    ).generate_markdown()

    assert "Fixed reference date: `2026-08-01`" in markdown
    assert "due_date < reference_today AND completed_date IS NULL" in markdown
    assert "tasks.status = 'InProgress'" in markdown
    assert "inclusive calendar-quarter start" in markdown
    assert "end_date IS NULL" in markdown
    assert "released_at IS NULL" in markdown
    assert "100 * completed_milestones / NULLIF(total_milestones, 0)" in markdown
    assert "ROUND_HALF_UP" in markdown
    assert "Task allocations sum to `100.00`" in markdown
    assert "USD only" in markdown


def test_dictionary_documents_controlled_imperfections() -> None:
    markdown = ProjectManagementDataDictionaryGenerator.for_profile(
        "full"
    ).generate_markdown()

    assert "Near-duplicate time entries: `1.0%`" in markdown
    assert "Open-ended project and task ranges: `2.5%`" in markdown
    assert "Task estimate outliers: `0.5%`" in markdown
    assert "`1900-01-01`" in markdown
    assert "Business-semantic NULLs" in markdown


def test_dictionary_is_deterministic() -> None:
    first = ProjectManagementDataDictionaryGenerator.for_profile(
        "full"
    ).generate_markdown()
    second = ProjectManagementDataDictionaryGenerator.for_profile(
        "full"
    ).generate_markdown()

    assert first == second


def test_dictionary_refuses_non_release_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        ProjectManagementDataDictionaryGenerator.for_profile(
            "dev"
        ).write_release_dictionary()


def test_dictionary_writes_unsealed_release_atomically(tmp_path: Path) -> None:
    generator = ProjectManagementDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_dictionary()

    assert output_path == tmp_path / "data_dictionary.md"
    assert output_path.read_text(encoding="utf-8") == generator.generate_markdown()
    assert not (tmp_path / "data_dictionary.md.tmp").exists()


def test_dictionary_refuses_sealed_release(tmp_path: Path) -> None:
    generator = ProjectManagementDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_dictionary()


def test_dictionary_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "full")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementDataDictionaryGenerator(DeterministicGenerator(settings))
