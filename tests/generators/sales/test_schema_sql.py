from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.manifest import compute_sha256
from generators.sales.schema_sql import SalesSchemaSQLGenerator


def test_sales_schema_sql_matches_canonical_ddl_contract() -> None:
    generator = SalesSchemaSQLGenerator.for_profile("full")
    sql = generator.generate_sql()

    assert sql == generator.settings.schema_source.read_text(encoding="utf-8")
    assert sql.encode("utf-8") == generator.settings.schema_source.read_bytes()
    for table_name in ("leads", "deals", "products", "quotations", "targets"):
        assert f"CREATE TABLE {table_name}" in sql
    assert "deal_amount DECIMAL(15, 2) NOT NULL" in sql
    assert "list_price DECIMAL(15, 2)" in sql
    assert "unit_price DECIMAL(15, 2) NOT NULL" in sql
    assert "quota_amount DECIMAL(15, 2) NOT NULL" in sql
    assert sql.count("CHECK (currency_code = 'USD')") == 3
    assert "CONSTRAINT fk_deals_lead" in sql
    assert "CONSTRAINT fk_quotations_deal" in sql
    assert "CONSTRAINT fk_quotations_product" in sql
    assert "FOREIGN KEY (rep_name)" not in sql


def test_sales_schema_sql_refuses_non_release_profile() -> None:
    generator = SalesSchemaSQLGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.write_release_schema()


def test_sales_schema_sql_writes_byte_identical_artifact_atomically(
    tmp_path: Path,
) -> None:
    generator = SalesSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_schema()

    assert output_path == tmp_path / "schema.sql"
    assert output_path.read_bytes() == generator.settings.schema_source.read_bytes()
    assert (
        compute_sha256(output_path).sha256
        == compute_sha256(generator.settings.schema_source).sha256
    )
    assert not (tmp_path / "schema.sql.tmp").exists()


def test_sales_schema_sql_refuses_sealed_release(tmp_path: Path) -> None:
    generator = SalesSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_schema()


def test_sales_schema_sql_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesSchemaSQLGenerator(DeterministicGenerator(settings))
