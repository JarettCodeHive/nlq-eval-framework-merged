from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.config import load_sales_config
from generators.sales.data_dictionary import FIELD_DESCRIPTIONS
from generators.sales.data_dictionary import SalesDataDictionaryGenerator
from generators.sales.data_dictionary import TABLE_DESCRIPTIONS
from generators.sales.data_dictionary import _type_text


def test_dictionary_includes_every_configured_table_and_field() -> None:
    generator = SalesDataDictionaryGenerator.for_profile("full")
    config = load_sales_config()
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
        assert TABLE_DESCRIPTIONS[table_name] in markdown
        for field in config["tables"][table_name]["fields"]:
            assert f"| `{field['name']}` | `{_type_text(field)}` |" in markdown


def test_dictionary_documents_values_distributions_and_generation_rules() -> None:
    markdown = SalesDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Allowed Domain Values" in markdown
    assert "`WebForm`, `Referral`, `EventBooth`, `ColdCall`, `Inbound`" in markdown
    assert "`Prospecting`, `Negotiation`, `Won`, `Lost`" in markdown
    assert "## Base Generation Rules" in markdown
    assert '"period_months": 3' in markdown
    assert "## Distribution Configuration" in markdown
    assert "`deal_amount` | `pareto_amount` | None" in markdown
    assert '"lambda":6.0' in markdown
    assert "`quotations` grouped by `deal_id`" in markdown
    assert "`targets.quota_amount`" in markdown


def test_dictionary_documents_sales_business_and_relationship_semantics() -> None:
    markdown = SalesDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "Sales is USD-only" in markdown
    assert "| `currency_code` | `char(3)` | `false` |  |  | `USD` |" in markdown
    assert "| `is_active` | `boolean` | `false` |  |  | `true` |" in markdown
    assert "| `rep_name` | `varchar(255)` | `false` | `analytical` |" in markdown
    assert "captured price at quote time" in markdown
    assert "`quantity * unit_price * (1 - discount_pct / 100)`" in markdown
    assert "`deals.stage = 'Won'`" in markdown
    assert "`100 * attained_revenue / targets.quota_amount`" in markdown
    assert "explicit Deal-to-Product many-to-many bridge" in markdown
    assert "analytical rather than a physical foreign key" in markdown
    assert "start-inclusive, end-exclusive target period" in markdown
    assert "`COUNT(*)` to count physical quote lines" in markdown
    assert "`COUNT(DISTINCT product_id)` for distinct products" in markdown


def test_dictionary_distinguishes_nulls_and_documents_imperfections() -> None:
    markdown = SalesDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Controlled Imperfections" in markdown
    assert "Near-duplicate quotation lines: `1.0%`" in markdown
    assert "Missing `products.list_price`: `2.5%`" in markdown
    assert "`deals.deal_amount` outliers: `0.5%`" in markdown
    assert "`1900-01-01T00:00:00`" in markdown
    assert "Business-semantic NULLs are separate from injected NULLs" in markdown
    assert "Only `products.list_price` is a controlled NULL-rate target" in markdown
    assert "Business-state NULL: Open deal without an actual close date" in markdown


def test_dictionary_documents_fixed_reference_date_and_date_boundaries() -> None:
    markdown = SalesDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "Fixed reference date: `2026-08-01`" in markdown
    assert "never the machine clock" in markdown
    assert "deliberate test values" in markdown


def test_dictionary_refuses_non_release_profile() -> None:
    generator = SalesDataDictionaryGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.write_release_dictionary()


def test_dictionary_writes_unsealed_release_atomically(tmp_path: Path) -> None:
    generator = SalesDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_dictionary()

    assert output_path == tmp_path / "data_dictionary.md"
    assert output_path.read_text(encoding="utf-8") == generator.generate_markdown()
    assert not (tmp_path / "data_dictionary.md.tmp").exists()


def test_dictionary_refuses_sealed_release(tmp_path: Path) -> None:
    generator = SalesDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_dictionary()


def test_dictionary_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesDataDictionaryGenerator(DeterministicGenerator(settings))


def test_type_text_formats_sales_types() -> None:
    assert _type_text({"type": "varchar", "max_length": 255}) == "varchar(255)"
    assert _type_text({"type": "char", "max_length": 3}) == "char(3)"
    assert (
        _type_text({"type": "decimal", "precision": 15, "scale": 2}) == "decimal(15,2)"
    )
    assert _type_text({"type": "timestamp"}) == "timestamp"
