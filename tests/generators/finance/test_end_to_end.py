"""End-to-end acceptance coverage for the Finance dataset pipeline."""

from __future__ import annotations

import importlib.util

import pytest

from generators.finance.export import FinanceCSVExporter
from generators.finance.validators.fk_integrity import FinanceDuckDBFKValidator
from generators.finance.validators.fk_integrity import load_finance_csvs
from generators.finance.validators.imperfection_rates import (
    FinanceImperfectionRateValidator,
)
from generators.finance.validators.join_paths import FinanceJoinPathValidator
from generators.finance.validators.relational import FinanceRelationalValidator
from generators.finance.validators.row_caps import FinanceRowCapValidator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def finance_dev_release(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build all dev stages once and export the final tables for acceptance tests."""

    output_dir = tmp_path_factory.mktemp("finance-dev-acceptance")
    exporter = FinanceCSVExporter.for_profile("dev")
    stages = exporter.generate_validated_stages()
    exporter.export_tables(stages["imperfect"], output_dir=output_dir)
    csv_paths = {
        table_name: output_dir / f"{table_name}.csv"
        for table_name in exporter.settings.table_order
    }
    return {
        "exporter": exporter,
        "stages": stages,
        "csv_paths": csv_paths,
    }


def test_one_exported_artifact_passes_every_finance_gate(
    finance_dev_release: dict,
) -> None:
    """Require one generated artifact to pass every pre-release validation gate."""

    exporter = finance_dev_release["exporter"]
    stages = finance_dev_release["stages"]
    csv_paths = finance_dev_release["csv_paths"]
    generator = exporter.generator

    gate_results = {
        "relational": FinanceRelationalValidator(generator).validate_tables(
            stages["imperfect"]
        ),
        "row_caps": FinanceRowCapValidator(generator).validate_tables(
            stages["imperfect"]
        ),
        "duckdb_fk": FinanceDuckDBFKValidator(generator)._validate_csv_paths(csv_paths),
        "join_paths": FinanceJoinPathValidator(generator)._validate_csv_paths(
            csv_paths
        ),
        "imperfections": FinanceImperfectionRateValidator(generator).validate_tables(
            stages["imperfect"], source_tables=stages["distributed"]
        ),
    }

    failures = {
        gate: [result.check_name for result in results if not result.passed]
        for gate, results in gate_results.items()
        if any(not result.passed for result in results)
    }
    assert not failures
    assert all(gate_results.values())


def test_exported_finance_data_supports_required_analytical_queries(
    finance_dev_release: dict,
) -> None:
    """Exercise FX sums, budget-versus-actual, and rolling-balance queries."""

    import duckdb

    exporter = finance_dev_release["exporter"]
    csv_paths = finance_dev_release["csv_paths"]
    ddl = exporter.settings.schema_source.read_text(encoding="utf-8")

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(ddl)
        load_results = load_finance_csvs(
            connection,
            csv_paths,
            exporter.settings.table_order,
        )
        assert all(result.passed for result in load_results)

        fx_rows, fx_total = connection.execute(
            """
            SELECT
                COUNT(*),
                SUM(transactions.total_amount * fx_rates.rate)
            FROM transactions
            INNER JOIN fx_rates
                ON transactions.source_currency = fx_rates.from_currency
                AND transactions.target_currency = fx_rates.to_currency
                AND transactions.transaction_date = fx_rates.rate_date
            WHERE fx_rates.rate IS NOT NULL
            """
        ).fetchone()
        budget_rows, budgets_with_actuals = connection.execute(
            """
            WITH actuals AS (
                SELECT
                    ledger_entries.account_id,
                    transactions.transaction_date,
                    SUM(
                        CASE
                            WHEN ledger_entries.debit_amount IS NOT NULL
                                THEN ledger_entries.debit_amount * fx_rates.rate
                            ELSE -ledger_entries.credit_amount * fx_rates.rate
                        END
                    ) AS actual_amount
                FROM ledger_entries
                INNER JOIN transactions
                    ON ledger_entries.transaction_id = transactions.transaction_id
                INNER JOIN fx_rates
                    ON transactions.source_currency = fx_rates.from_currency
                    AND transactions.target_currency = fx_rates.to_currency
                    AND transactions.transaction_date = fx_rates.rate_date
                WHERE fx_rates.rate IS NOT NULL
                GROUP BY
                    ledger_entries.account_id,
                    transactions.transaction_date
            )
            SELECT
                COUNT(*),
                COUNT(actuals.actual_amount)
            FROM budgets
            LEFT JOIN actuals
                ON budgets.account_id = actuals.account_id
                AND actuals.transaction_date >= budgets.period_start
                AND actuals.transaction_date < budgets.period_end
            """
        ).fetchone()
        rolling_rows, populated_balances = connection.execute(
            """
            WITH postings AS (
                SELECT
                    ledger_entries.account_id,
                    transactions.transaction_date,
                    ledger_entries.entry_id,
                    CASE
                        WHEN ledger_entries.debit_amount IS NOT NULL
                            THEN ledger_entries.debit_amount
                        ELSE -ledger_entries.credit_amount
                    END AS signed_amount
                FROM ledger_entries
                INNER JOIN transactions
                    ON ledger_entries.transaction_id = transactions.transaction_id
            ), rolling AS (
                SELECT
                    SUM(signed_amount) OVER (
                        PARTITION BY account_id
                        ORDER BY transaction_date, entry_id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS running_balance
                FROM postings
            )
            SELECT COUNT(*), COUNT(running_balance)
            FROM rolling
            """
        ).fetchone()

    assert fx_rows > 0
    assert fx_total is not None
    assert budget_rows > 0
    assert budgets_with_actuals > 0
    assert rolling_rows > 0
    assert populated_balances == rolling_rows
