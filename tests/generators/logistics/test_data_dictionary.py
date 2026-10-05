from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.logistics.config import load_logistics_config
from generators.logistics.data_dictionary import FIELD_DESCRIPTIONS
from generators.logistics.data_dictionary import LogisticsDataDictionaryGenerator
from generators.logistics.data_dictionary import TABLE_DESCRIPTIONS
from generators.logistics.data_dictionary import _type_text


def test_dictionary_covers_every_table_and_field() -> None:
    generator = LogisticsDataDictionaryGenerator.for_profile("full")
    config = load_logistics_config()
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


def test_dictionary_documents_logistics_contracts() -> None:
    markdown = LogisticsDataDictionaryGenerator.for_profile("full").generate_markdown()

    assert "## Distribution Configuration" in markdown
    assert "## Required Join Paths" in markdown
    assert "Order-to-Carrier many-to-many path" in markdown
    assert "intentionally has no physical FK" in markdown
    assert "COUNT(DISTINCT tracking_number)" in markdown
    assert "one logical snapshot per `(warehouse_id, product_sku)`" in markdown
    assert "USD-only" in markdown
    assert "Declared warehouse orphans: `1.0%`" in markdown
    assert "Near-duplicate shipments: `1.0%`" in markdown
    assert "Missing warehouse assignments: `2.5%`" in markdown


def test_dictionary_is_deterministic() -> None:
    generator = LogisticsDataDictionaryGenerator.for_profile("full")

    assert generator.generate_markdown() == generator.generate_markdown()


def test_dictionary_write_guards_and_atomic_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsDataDictionaryGenerator.for_profile("dev").write_release_dictionary()

    generator = LogisticsDataDictionaryGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    output = generator.write_release_dictionary()

    assert output.read_text(encoding="utf-8") == generator.generate_markdown()
    assert not (tmp_path / "data_dictionary.md.tmp").exists()
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_dictionary()
