from __future__ import annotations

import importlib.util

import pytest

import generators.sales.generator as generator_module
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.validators.generated_tables import (
    SalesGeneratedTablesValidator,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def valid_generation() -> tuple[SalesBaseEntityGenerator, dict[str, object]]:
    generator = SalesBaseEntityGenerator.for_profile("dev")
    return generator, generator.generate_tables()


def _copy_tables(tables: dict[str, object]) -> dict[str, object]:
    return {
        table_name: table.copy(deep=True)  # type: ignore[attr-defined]
        for table_name, table in tables.items()
    }


def test_immediate_validator_accepts_clean_base_tables(valid_generation: tuple) -> None:
    generator, tables = valid_generation

    SalesGeneratedTablesValidator(generator.generator).validate(tables)


def test_generator_invokes_immediate_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    def validate(context: object, tables: object) -> None:
        calls.append((context, tables))

    monkeypatch.setattr(generator_module, "validate_sales_generated_tables", validate)
    generator = SalesBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()

    assert calls == [(generator.generator, tables)]


def test_guard_rejects_table_order_drift(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    mutated = _copy_tables(tables)
    mutated = {"deals": mutated.pop("deals"), **mutated}

    with pytest.raises(ValueError, match="table order differs"):
        SalesGeneratedTablesValidator(generator.generator).validate(mutated)


def test_guard_rejects_column_and_row_drift(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    missing_column = _copy_tables(tables)
    missing_column["products"] = missing_column["products"].drop(  # type: ignore[attr-defined]
        columns=["category"]
    )
    with pytest.raises(ValueError, match="products generated columns differ"):
        SalesGeneratedTablesValidator(generator.generator).validate(missing_column)

    missing_row = _copy_tables(tables)
    missing_row["targets"] = missing_row["targets"].iloc[:-1].copy()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="targets generated row count differs"):
        SalesGeneratedTablesValidator(generator.generator).validate(missing_row)


def test_guard_rejects_primary_and_required_field_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    duplicate_key = _copy_tables(tables)
    duplicate_key["leads"].loc[1, "lead_id"] = duplicate_key["leads"].loc[  # type: ignore[attr-defined]
        0, "lead_id"
    ]
    with pytest.raises(ValueError, match="leads contains duplicate primary keys"):
        SalesGeneratedTablesValidator(generator.generator).validate(duplicate_key)

    blank_required = _copy_tables(tables)
    blank_required["products"].loc[0, "product_name"] = ""  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="product_name.*blank required"):
        SalesGeneratedTablesValidator(generator.generator).validate(blank_required)


def test_guard_rejects_orphan_and_ownership_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    orphan = _copy_tables(tables)
    orphan["quotations"].loc[0, "deal_id"] = 999999  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="quotations.deal_id contains orphan"):
        SalesGeneratedTablesValidator(generator.generator).validate(orphan)

    ownership = _copy_tables(tables)
    ownership["deals"].loc[0, "rep_name"] = "Wrong Representative"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="representative differs"):
        SalesGeneratedTablesValidator(generator.generator).validate(ownership)


def test_guard_rejects_conversion_and_quotation_chronology_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    conversion = _copy_tables(tables)
    source_lead = conversion["deals"].loc[0, "lead_id"]  # type: ignore[attr-defined]
    conversion["leads"].loc[  # type: ignore[attr-defined]
        conversion["leads"]["lead_id"] == source_lead, "lead_status"  # type: ignore[index]
    ] = "New"
    with pytest.raises(ValueError, match="lead that is not Converted"):
        SalesGeneratedTablesValidator(generator.generator).validate(conversion)

    chronology = _copy_tables(tables)
    chronology["quotations"].loc[0, "quoted_at"] = "1900-01-01T00:00:00"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="timestamps are outside"):
        SalesGeneratedTablesValidator(generator.generator).validate(chronology)


def test_guard_rejects_bridge_cardinality_corruption(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    mutated = _copy_tables(tables)
    first_deal = mutated["quotations"].loc[0, "deal_id"]  # type: ignore[attr-defined]
    deal_rows = mutated["quotations"]["deal_id"] == first_deal  # type: ignore[index]
    first_product = mutated["quotations"].loc[deal_rows, "product_id"].iloc[0]  # type: ignore[attr-defined]
    mutated["quotations"].loc[deal_rows, "product_id"] = first_product  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="duplicate deal/product pairs"):
        SalesGeneratedTablesValidator(generator.generator).validate(mutated)


def test_guard_rejects_target_period_and_territory_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    overlap = _copy_tables(tables)
    first_rep = overlap["targets"].loc[0, "rep_name"]  # type: ignore[attr-defined]
    positions = overlap["targets"].index[  # type: ignore[attr-defined]
        overlap["targets"]["rep_name"] == first_rep  # type: ignore[index]
    ]
    overlap["targets"].loc[positions[1], "period_start"] = overlap["targets"].loc[  # type: ignore[attr-defined]
        positions[0], "period_start"
    ]
    with pytest.raises(ValueError, match="periods overlap"):
        SalesGeneratedTablesValidator(generator.generator).validate(overlap)

    territory = _copy_tables(tables)
    territory["targets"].loc[0, "territory"] = "Wrong Territory"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="multiple territories|territories differ"):
        SalesGeneratedTablesValidator(generator.generator).validate(territory)


def test_guard_rejects_missing_quota_path_and_non_usd_values(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    no_attainment = _copy_tables(tables)
    won_stage = generator.sales_config["business_mappings"]["quota_attainment"][
        "attained_stage"
    ]
    replacement = next(
        stage
        for stage in generator.generation_rules["deals"]["closed_stages"]
        if stage != won_stage
    )
    no_attainment["deals"].loc[  # type: ignore[attr-defined]
        no_attainment["deals"]["stage"] == won_stage, "stage"  # type: ignore[index]
    ] = replacement
    with pytest.raises(ValueError, match="lacks a non-empty quota-attainment path"):
        SalesGeneratedTablesValidator(generator.generator).validate(no_attainment)

    currency = _copy_tables(tables)
    currency["products"].loc[0, "currency_code"] = "EUR"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="products contains non-USD"):
        SalesGeneratedTablesValidator(generator.generator).validate(currency)
