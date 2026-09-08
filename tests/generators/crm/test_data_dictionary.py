from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.crm.config import load_crm_config
from generators.crm.data_dictionary import CRMDataDictionaryGenerator
from generators.crm.data_dictionary import FIELD_DESCRIPTIONS
from generators.crm.data_dictionary import TABLE_DESCRIPTIONS
from generators.crm.data_dictionary import _type_text


def test_data_dictionary_includes_all_configured_tables_and_fields() -> None:
    generator = CRMDataDictionaryGenerator.for_profile("full")
    crm_config = load_crm_config()
    markdown = generator.generate_markdown()

    assert list(TABLE_DESCRIPTIONS) == crm_config["table_order"]
    expected_fields = {
        f"{table_name}.{field['name']}"
        for table_name in crm_config["table_order"]
        for field in crm_config["tables"][table_name]["fields"]
    }
    assert set(FIELD_DESCRIPTIONS) == expected_fields

    for table_name in crm_config["table_order"]:
        assert f"### `{table_name}`" in markdown
        assert TABLE_DESCRIPTIONS[table_name] in markdown
        for field in crm_config["tables"][table_name]["fields"]:
            field_row = next(
                line
                for line in markdown.splitlines()
                if line.startswith(f"| `{field['name']}` |")
            )
            row_cells = [cell.strip() for cell in field_row.strip("|").split("|")]
            assert row_cells[5]
            assert row_cells[6]
            assert row_cells[7]


def test_data_dictionary_documents_engagement_business_rules() -> None:
    markdown = CRMDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "CRM campaign monetary values are USD-only" in markdown
    assert "## Domain Values" in markdown
    assert "## Base Generation Rules" in markdown
    assert '"customer_tier_weights"' in markdown
    assert "## Distribution Configuration" in markdown
    assert "reusable defaults, not mandatory values for every domain" in markdown
    assert "`campaign_budget` | `pareto_amount` | None" in markdown
    assert '"alpha":1.16' in markdown
    assert "`campaigns.budget_amount`" in markdown
    assert "`interactions` grouped by `contact_id`" in markdown
    assert "`support_cases.opened_at`" in markdown
    assert "### Engagement Points" in markdown
    assert "| `Meeting` | 10 | `Phone` |" in markdown
    assert "### Campaign Attribution" in markdown
    assert "Complete pre-imperfection weights sum to `1.0000`" in markdown
    assert "### Support-Case SLA" in markdown
    assert "| `Critical` | 4 |" in markdown
    assert "## Derived Metrics" in markdown
    assert "Resolved within SLA" in markdown


def test_data_dictionary_documents_current_relationships() -> None:
    markdown = CRMDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Relationships" in markdown
    assert "`accounts.account_id` | `contacts.account_id`" in markdown
    assert "`contacts.contact_id` | `contact_campaigns.contact_id`" in markdown
    assert "`campaigns.campaign_id` | `contact_campaigns.campaign_id`" in markdown
    assert "`accounts.account_id` | `support_cases.account_id`" in markdown
    assert "explicit many-to-many bridge" in markdown


def test_data_dictionary_distinguishes_business_nulls_and_imperfections() -> None:
    markdown = CRMDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Controlled Imperfections" in markdown
    assert "Typo-based near-duplicate contacts: `1.0%`" in markdown
    assert "Missing `contact_campaigns.attribution_weight`: `2.5%`" in markdown
    assert "`interactions.engagement_points` outliers" in markdown
    assert "Business-state NULLs are separate from controlled imperfections" in markdown
    assert "Business-state NULL: Organic or unattributed interaction." in markdown
    assert "Receives each configured boundary timestamp." in markdown


def test_data_dictionary_contains_no_retired_pipeline_contract() -> None:
    markdown = CRMDataDictionaryGenerator.for_profile("full").generate_markdown()

    for retired_name in (
        "opportunities",
        "contact_opportunities",
        "activities",
        "deal_amount",
        "close_date",
        "loss_reason",
    ):
        assert retired_name not in markdown


def test_data_dictionary_refuses_non_release_profile() -> None:
    generator = CRMDataDictionaryGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        generator.write_release_dictionary()


def test_data_dictionary_writes_unsealed_release_atomically(tmp_path: Path) -> None:
    generator = CRMDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_dictionary()

    assert output_path == tmp_path / "data_dictionary.md"
    assert output_path.read_text(encoding="utf-8") == generator.generate_markdown()
    assert not (tmp_path / "data_dictionary.md.tmp").exists()


def test_data_dictionary_refuses_sealed_release(tmp_path: Path) -> None:
    generator = CRMDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_dictionary()


def test_type_text_formats_sized_and_decimal_types() -> None:
    assert _type_text({"type": "varchar", "max_length": 255}) == "varchar(255)"
    assert _type_text({"type": "char", "max_length": 3}) == "char(3)"
    assert (
        _type_text({"type": "decimal", "precision": 15, "scale": 2}) == "decimal(15,2)"
    )
    assert _type_text({"type": "integer"}) == "integer"
