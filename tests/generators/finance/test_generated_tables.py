from __future__ import annotations

from decimal import Decimal
import importlib.util

import pytest

import generators.finance.generator as generator_module
from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.validators.generated_tables import (
    FinanceGeneratedTablesValidator,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None for package in ("numpy", "pandas")
    ),
    reason="numpy and pandas are required",
)


@pytest.fixture(scope="module")
def valid_generation() -> tuple[FinanceBaseEntityGenerator, dict[str, object]]:
    generator = FinanceBaseEntityGenerator.for_profile("dev")
    return generator, generator.generate_tables()


def _copy_tables(tables: dict[str, object]) -> dict[str, object]:
    return {
        table_name: table.copy(deep=True)  # type: ignore[attr-defined]
        for table_name, table in tables.items()
    }


def test_immediate_validator_accepts_clean_base_tables(valid_generation: tuple) -> None:
    generator, tables = valid_generation

    FinanceGeneratedTablesValidator(generator.generator).validate(tables)


def test_generator_invokes_immediate_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    def validate(context: object, tables: object) -> None:
        calls.append((context, tables))

    monkeypatch.setattr(generator_module, "validate_finance_generated_tables", validate)
    generator = FinanceBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()

    assert calls == [(generator.generator, tables)]


def test_guard_rejects_non_finance_settings() -> None:
    context = DeterministicGenerator(
        GenerationSettings.from_config_files("sales", "dev")
    )

    with pytest.raises(ValueError, match="only supports the finance domain"):
        FinanceGeneratedTablesValidator(context)


def test_guard_rejects_table_column_and_row_drift(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    reordered = _copy_tables(tables)
    reordered = {"transactions": reordered.pop("transactions"), **reordered}
    with pytest.raises(ValueError, match="table order differs"):
        FinanceGeneratedTablesValidator(generator.generator).validate(reordered)

    missing_column = _copy_tables(tables)
    missing_column["fx_rates"] = missing_column["fx_rates"].drop(  # type: ignore[attr-defined]
        columns=["rate_source"]
    )
    with pytest.raises(ValueError, match="fx_rates generated columns differ"):
        FinanceGeneratedTablesValidator(generator.generator).validate(missing_column)

    missing_row = _copy_tables(tables)
    missing_row["budgets"] = missing_row["budgets"].iloc[:-1].copy()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="budgets generated row count differs"):
        FinanceGeneratedTablesValidator(generator.generator).validate(missing_row)


def test_guard_rejects_primary_required_and_unique_key_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    duplicate_key = _copy_tables(tables)
    duplicate_key["accounts"].loc[1, "account_id"] = duplicate_key[  # type: ignore[attr-defined]
        "accounts"
    ].loc[
        0, "account_id"
    ]
    with pytest.raises(ValueError, match="accounts contains duplicate primary keys"):
        FinanceGeneratedTablesValidator(generator.generator).validate(duplicate_key)

    blank_required = _copy_tables(tables)
    blank_required["transactions"].loc[0, "source_system"] = ""  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="source_system.*blank required"):
        FinanceGeneratedTablesValidator(generator.generator).validate(blank_required)

    duplicate_composite = _copy_tables(tables)
    for field in ("from_currency", "to_currency", "rate_date"):
        duplicate_composite["fx_rates"].loc[1, field] = duplicate_composite[  # type: ignore[attr-defined]
            "fx_rates"
        ].loc[
            0, field
        ]
    with pytest.raises(ValueError, match="fx_rates contains duplicate unique key"):
        FinanceGeneratedTablesValidator(generator.generator).validate(
            duplicate_composite
        )


def test_guard_rejects_physical_fk_orphans(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    orphan = _copy_tables(tables)
    orphan["ledger_entries"].loc[0, "account_id"] = 999999  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="ledger_entries.account_id contains orphan"):
        FinanceGeneratedTablesValidator(generator.generator).validate(orphan)


def test_guard_rejects_account_mapping_and_hierarchy_cycles(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    mapping = _copy_tables(tables)
    mapping["accounts"].loc[0, "normal_balance"] = "Wrong"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="normal balance differs"):
        FinanceGeneratedTablesValidator(generator.generator).validate(mapping)

    cycle = _copy_tables(tables)
    accounts = cycle["accounts"]
    child_position = accounts.index[accounts["parent_account_id"] != ""][0]  # type: ignore[attr-defined,index]
    child_id = int(accounts.loc[child_position, "account_id"])  # type: ignore[attr-defined]
    parent_id = int(accounts.loc[child_position, "parent_account_id"])  # type: ignore[attr-defined]
    accounts.loc[accounts["account_id"] == parent_id, "parent_account_id"] = child_id  # type: ignore[attr-defined,index]
    with pytest.raises(ValueError, match="hierarchy contains a cycle"):
        FinanceGeneratedTablesValidator(generator.generator).validate(cycle)


def test_guard_rejects_fx_identity_and_transaction_lookup_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    identity = _copy_tables(tables)
    usd_position = identity["fx_rates"].index[  # type: ignore[attr-defined]
        identity["fx_rates"]["from_currency"] == "USD"  # type: ignore[index]
    ][0]
    identity["fx_rates"].loc[usd_position, "rate"] = "1.000001"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="identity rates differ"):
        FinanceGeneratedTablesValidator(generator.generator).validate(identity)

    orphan = _copy_tables(tables)
    orphan["transactions"].loc[0, "transaction_date"] = "2020-01-01"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="FX-key orphan"):
        FinanceGeneratedTablesValidator(generator.generator).validate(orphan)


def test_guard_rejects_decimal_scale_and_boundary_date_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    scale = _copy_tables(tables)
    scale["transactions"].loc[0, "total_amount"] = "10.00"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="total_amount must use scale 4"):
        FinanceGeneratedTablesValidator(generator.generator).validate(scale)

    boundary = _copy_tables(tables)
    transaction = boundary["transactions"].iloc[0]  # type: ignore[attr-defined]
    boundary_date = generator.base_config["imperfections"]["boundary_dates"][0]
    boundary["transactions"].loc[0, "transaction_date"] = boundary_date  # type: ignore[attr-defined]
    matching_rate = boundary["fx_rates"][  # type: ignore[index]
        (boundary["fx_rates"]["from_currency"] == transaction.source_currency)  # type: ignore[index]
        & (boundary["fx_rates"]["rate_date"] == boundary_date)  # type: ignore[index]
    ]
    assert not matching_rate.empty
    with pytest.raises(ValueError, match="contain boundary dates"):
        FinanceGeneratedTablesValidator(generator.generator).validate(boundary)


def test_guard_rejects_ledger_side_and_balance_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    both_sides = _copy_tables(tables)
    both_sides["ledger_entries"].loc[  # type: ignore[attr-defined]
        0, ["debit_amount", "credit_amount"]
    ] = ["1.0000", "1.0000"]
    with pytest.raises(ValueError, match="exactly one debit or credit"):
        FinanceGeneratedTablesValidator(generator.generator).validate(both_sides)

    imbalance = _copy_tables(tables)
    debit_positions = imbalance["ledger_entries"].index[  # type: ignore[attr-defined]
        imbalance["ledger_entries"]["debit_amount"] != ""  # type: ignore[index]
    ]
    position = debit_positions[0]
    original = Decimal(imbalance["ledger_entries"].loc[position, "debit_amount"])  # type: ignore[attr-defined]
    imbalance["ledger_entries"].loc[position, "debit_amount"] = (  # type: ignore[attr-defined]
        f"{original + Decimal('0.0001'):.4f}"
    )
    with pytest.raises(ValueError, match="debit and credit totals differ"):
        FinanceGeneratedTablesValidator(generator.generator).validate(imbalance)


def test_guard_rejects_ledger_sequence_and_unposted_count_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    sequence = _copy_tables(tables)
    sequence["ledger_entries"].loc[0, "line_number"] = 99  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="line numbers are not sequential"):
        FinanceGeneratedTablesValidator(generator.generator).validate(sequence)

    coverage = _copy_tables(tables)
    posted = set(coverage["ledger_entries"]["transaction_id"])  # type: ignore[index]
    unposted_id = next(
        value
        for value in coverage["transactions"]["transaction_id"]  # type: ignore[index]
        if value not in posted
    )
    coverage["ledger_entries"].loc[0, "transaction_id"] = unposted_id  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="zero-ledger transaction count differs"):
        FinanceGeneratedTablesValidator(generator.generator).validate(coverage)


def test_guard_rejects_budget_period_and_scenario_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    overlap = _copy_tables(tables)
    first_account = overlap["budgets"].loc[0, "account_id"]  # type: ignore[attr-defined]
    positions = overlap["budgets"].index[  # type: ignore[attr-defined]
        overlap["budgets"]["account_id"] == first_account  # type: ignore[index]
    ]
    overlap["budgets"].loc[positions[1], "period_start"] = overlap[  # type: ignore[attr-defined]
        "budgets"
    ].loc[
        positions[0], "period_start"
    ]
    with pytest.raises(ValueError, match="periods overlap"):
        FinanceGeneratedTablesValidator(generator.generator).validate(overlap)

    scenario = _copy_tables(tables)
    scenario["budgets"].loc[0, "scenario"] = "Unknown"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="unknown scenarios"):
        FinanceGeneratedTablesValidator(generator.generator).validate(scenario)
