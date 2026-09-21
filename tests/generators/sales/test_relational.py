from __future__ import annotations

import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.generator import SALES_COLUMN_CONTRACTS
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.relational import SalesRelationalValidator


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return SalesImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def test_generated_relational_validation_passes() -> None:
    validator = SalesRelationalValidator.for_profile("dev")
    results = validator.generate_and_validate()
    checks = _results_by_name(results)

    assert validator.settings.table_order == tuple(SALES_COLUMN_CONTRACTS)
    assert results and all(result.passed for result in results)
    assert "deals.lead_id.fk" in checks
    assert "quotations.deal_many_products" in checks
    assert "quotations.product_many_deals" in checks
    assert "targets.won_deal_attainment" in checks
    assert "targets.zero_attainment" in checks
    assert "deals.currency_code" in checks


def test_missing_table_reports_contract_failure_without_crashing(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed.pop("products")
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["schema.table_order"].passed
    assert not checks["products.present"].passed


def test_wrong_column_order_stops_dependent_checks(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    columns = malformed["deals"].columns.tolist()
    columns[0], columns[1] = columns[1], columns[0]
    malformed["deals"] = malformed["deals"][columns]
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["deals.columns"].passed
    assert "deals.row_count" not in checks


def test_key_fk_required_and_row_count_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["quotations"].loc[1, "quotation_id"] = malformed["quotations"].loc[
        0, "quotation_id"
    ]
    malformed["quotations"].loc[2, "product_id"] = 999999
    malformed["products"].loc[0, "product_name"] = ""
    malformed["targets"] = malformed["targets"].iloc[:-1]
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["quotations.primary_key"].passed
    assert not checks["quotations.product_id.fk"].passed
    assert not checks["products.product_name.not_null"].passed
    assert not checks["targets.row_count"].passed


def test_lead_ownership_status_and_unmatched_semantics_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    lead_id = malformed["deals"].loc[0, "lead_id"]
    lead_position = malformed["leads"].index[malformed["leads"]["lead_id"] == lead_id][
        0
    ]
    malformed["leads"].loc[lead_position, "lead_status"] = "Qualified"
    malformed["deals"].loc[0, "rep_name"] = "Different Representative"
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["deals.converted_lead"].passed
    assert not checks["deals.lead_owner_consistency"].passed


def test_product_quotation_and_temporal_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    priced = malformed["products"].index[
        malformed["products"]["list_price"].astype(str).ne("")
    ][0]
    malformed["products"].loc[priced, "list_price"] = "-1.00"
    malformed["quotations"].loc[0, "quantity"] = 0
    malformed["quotations"].loc[1, "discount_pct"] = "99.00"
    malformed["quotations"].loc[2, "created_at"] = "2099-01-01T00:00:00"
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["products.list_price.positive"].passed
    assert not checks["quotations.quantity.range"].passed
    assert not checks["quotations.discount_pct.range"].passed
    assert not checks["quotations.temporal"].passed


def test_deal_target_representative_and_currency_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    closed_position = malformed["deals"].index[
        malformed["deals"]["close_date"].astype(str).ne("")
    ][0]
    malformed["deals"].loc[closed_position, "close_date"] = ""
    rep_name = malformed["targets"].loc[0, "rep_name"]
    rep_positions = malformed["targets"].index[
        malformed["targets"]["rep_name"] == rep_name
    ]
    malformed["targets"].loc[rep_positions[1], "period_start"] = malformed[
        "targets"
    ].loc[rep_positions[0], "period_start"]
    malformed["targets"].loc[0, "territory"] = "Different Territory"
    malformed["deals"].loc[0, "currency_code"] = "EUR"
    checks = _results_by_name(
        SalesRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["deals.stage_close_date_semantics"].passed
    assert not checks["targets.period_overlap"].passed
    assert not checks["sales.representative_alignment"].passed
    assert not checks["deals.currency_code"].passed


def test_validate_or_raise_combines_failures(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["deals"].loc[0, "currency_code"] = "EUR"
    malformed["products"].loc[0, "product_name"] = ""

    with pytest.raises(ValueError, match="Integrity validation failed"):
        SalesRelationalValidator.for_profile("dev").validate_or_raise(malformed)


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports sales"):
        SalesRelationalValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
