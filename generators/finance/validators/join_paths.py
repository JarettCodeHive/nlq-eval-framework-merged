"""DuckDB validation for required Finance analytical join paths."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.fk_integrity import load_finance_csvs
from generators.finance.validators.relational import FinanceRelationalValidator


REGISTERED_JOIN_SQL = {
    "finance_jp_005": """
        SELECT COUNT(*)
        FROM accounts AS child_account
        LEFT JOIN accounts AS parent_account
            ON child_account.parent_account_id = parent_account.account_id
    """,
    "finance_jp_006": """
        SELECT COUNT(*)
        FROM transactions
        INNER JOIN fx_rates
            ON transactions.source_currency = fx_rates.from_currency
            AND transactions.target_currency = fx_rates.to_currency
            AND transactions.transaction_date = fx_rates.rate_date
    """,
    "finance_jp_009": """
        SELECT COUNT(*)
        FROM transactions
        INNER JOIN ledger_entries
            ON transactions.transaction_id = ledger_entries.transaction_id
        INNER JOIN accounts
            ON ledger_entries.account_id = accounts.account_id
    """,
    "finance_jp_010": """
        WITH daily_actuals AS (
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
                AND fx_rates.rate IS NOT NULL
            GROUP BY
                ledger_entries.account_id,
                transactions.transaction_date
        ), period_actuals AS (
            SELECT
                budgets.budget_id,
                SUM(daily_actuals.actual_amount) AS actual_amount
            FROM budgets
            LEFT JOIN daily_actuals
                ON budgets.account_id = daily_actuals.account_id
                AND daily_actuals.transaction_date >= budgets.period_start
                AND daily_actuals.transaction_date < budgets.period_end
            GROUP BY budgets.budget_id
        )
        SELECT COUNT(*)
        FROM budgets
        LEFT JOIN period_actuals
            ON budgets.budget_id = period_actuals.budget_id
    """,
}


MATCHED_COUNT_SQL = {
    "finance_jp_005": """
        SELECT COUNT(*)
        FROM accounts AS child_account
        INNER JOIN accounts AS parent_account
            ON child_account.parent_account_id = parent_account.account_id
    """,
    "finance_jp_010": """
        WITH daily_actuals AS (
            SELECT DISTINCT
                ledger_entries.account_id,
                transactions.transaction_date
            FROM ledger_entries
            INNER JOIN transactions
                ON ledger_entries.transaction_id = transactions.transaction_id
            INNER JOIN fx_rates
                ON transactions.source_currency = fx_rates.from_currency
                AND transactions.target_currency = fx_rates.to_currency
                AND transactions.transaction_date = fx_rates.rate_date
                AND fx_rates.rate IS NOT NULL
        )
        SELECT COUNT(DISTINCT budgets.budget_id)
        FROM budgets
        INNER JOIN daily_actuals
            ON budgets.account_id = daily_actuals.account_id
            AND daily_actuals.transaction_date >= budgets.period_start
            AND daily_actuals.transaction_date < budgets.period_end
    """,
}


UNMATCHED_COUNT_SQL = {
    "finance_jp_005": """
        SELECT COUNT(*)
        FROM accounts AS child_account
        LEFT JOIN accounts AS parent_account
            ON child_account.parent_account_id = parent_account.account_id
        WHERE parent_account.account_id IS NULL
    """,
    "finance_jp_010": """
        WITH daily_actuals AS (
            SELECT DISTINCT
                ledger_entries.account_id,
                transactions.transaction_date
            FROM ledger_entries
            INNER JOIN transactions
                ON ledger_entries.transaction_id = transactions.transaction_id
            INNER JOIN fx_rates
                ON transactions.source_currency = fx_rates.from_currency
                AND transactions.target_currency = fx_rates.to_currency
                AND transactions.transaction_date = fx_rates.rate_date
                AND fx_rates.rate IS NOT NULL
        )
        SELECT COUNT(*)
        FROM budgets
        WHERE NOT EXISTS (
            SELECT 1
            FROM daily_actuals
            WHERE budgets.account_id = daily_actuals.account_id
              AND daily_actuals.transaction_date >= budgets.period_start
              AND daily_actuals.transaction_date < budgets.period_end
        )
    """,
}


FX_LOOKUP_INVALID_SQL = {
    "finance_jp_006.lookup_coverage": """
        SELECT COUNT(*)
        FROM transactions
        LEFT JOIN fx_rates
            ON transactions.source_currency = fx_rates.from_currency
            AND transactions.target_currency = fx_rates.to_currency
            AND transactions.transaction_date = fx_rates.rate_date
        WHERE fx_rates.rate_id IS NULL
    """,
    "finance_jp_006.unique_lookup": """
        SELECT COUNT(*)
        FROM (
            SELECT transactions.transaction_id
            FROM transactions
            INNER JOIN fx_rates
                ON transactions.source_currency = fx_rates.from_currency
                AND transactions.target_currency = fx_rates.to_currency
                AND transactions.transaction_date = fx_rates.rate_date
            GROUP BY transactions.transaction_id
            HAVING COUNT(*) <> 1
        ) AS invalid_lookups
    """,
}


MANY_TO_MANY_CARDINALITY_SQL = {
    "ledger_entries.transaction_many_accounts": """
        SELECT COUNT(*)
        FROM (
            SELECT transaction_id
            FROM ledger_entries
            GROUP BY transaction_id
            HAVING COUNT(DISTINCT account_id) > 1
        ) AS qualifying_transactions
    """,
    "ledger_entries.account_many_transactions": """
        SELECT COUNT(*)
        FROM (
            SELECT account_id
            FROM ledger_entries
            GROUP BY account_id
            HAVING COUNT(DISTINCT transaction_id) > 1
        ) AS qualifying_accounts
    """,
}


class FinanceJoinPathValidator:
    """Validate configured Finance physical and analytical join behavior."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceJoinPathValidator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.finance_config = load_finance_config()
        configured_registered_ids = {
            path["id"]
            for path in self.finance_config["join_path_requirements"]
            if len(path["tables"]) > 2
            or path["join_type"] == "left_self"
            or path["required_result"] == "complete_unique_lookup_coverage"
        }
        if configured_registered_ids != set(REGISTERED_JOIN_SQL):
            raise ValueError(
                "Registered Finance join SQL differs from configured complex paths"
            )

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceJoinPathValidator":
        """Create a Finance join-path validator from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate joins against CSVs in the configured output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate join paths in one complete persisted Finance CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final Finance data and validate joins through temporary CSVs."""

        tables = FinanceImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        FinanceRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="finance-join-validation-") as temp_dir:
            directory = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=directory,
            )
            return self._validate_csv_paths(self._csv_paths(directory))

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate configured exports and raise one combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Finance CSVs are missing. Export the requested profile first or "
                "use generated validation. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        return paths

    def _validate_csv_paths(
        self,
        csv_paths: dict[str, Path],
    ) -> list[IntegrityCheckResult]:
        duckdb = _require_duckdb()
        ddl = self.settings.schema_source.read_text(encoding="utf-8")
        results: list[IntegrityCheckResult] = []
        with duckdb.connect(database=":memory:") as connection:
            try:
                connection.execute(ddl)
            except Exception as exc:
                return [
                    failed(
                        "schema.duckdb_ddl",
                        f"failed to execute canonical DDL: {exc}",
                    )
                ]
            results.append(
                passed("schema.duckdb_ddl", "canonical DDL executed successfully")
            )
            load_results = load_finance_csvs(
                connection,
                csv_paths,
                self.settings.table_order,
            )
            results.extend(load_results)
            if any(not result.passed for result in load_results):
                return results

            for join_path in self.finance_config["join_path_requirements"]:
                results.extend(self._validate_join_path(connection, join_path))
            results.extend(self._validate_many_to_many_cardinality(connection))
        return results

    def _validate_join_path(
        self,
        connection: Any,
        join_path: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        join_id = join_path["id"]
        results = [
            _positive_count_result(
                f"{join_id}.joined_rows",
                _count(connection, _join_count_sql(join_path)),
                "join returned no rows",
            )
        ]
        required = join_path["required_result"]
        if required in {
            "non_empty_with_unmatched_parent_rows",
            "non_empty_with_matched_and_unmatched_rows",
            "non_empty_with_matched_and_unmatched_parent_rows",
        }:
            results.append(
                _positive_count_result(
                    f"{join_id}.unmatched_parent_rows",
                    _count(connection, _unmatched_count_sql(join_path)),
                    "LEFT JOIN path has no unmatched parent rows",
                )
            )
        if required in {
            "non_empty_with_matched_and_unmatched_rows",
            "non_empty_with_matched_and_unmatched_parent_rows",
        }:
            results.append(
                _positive_count_result(
                    f"{join_id}.matched_parent_rows",
                    _count(connection, MATCHED_COUNT_SQL[join_id]),
                    "LEFT JOIN path has no matched parent rows",
                )
            )
        if required == "complete_unique_lookup_coverage":
            for check_name, sql in FX_LOOKUP_INVALID_SQL.items():
                results.append(
                    _zero_count_result(
                        check_name,
                        _count(connection, sql),
                        "FX lookup coverage or uniqueness failed",
                    )
                )
        return results

    def _validate_many_to_many_cardinality(
        self,
        connection: Any,
    ) -> list[IntegrityCheckResult]:
        return [
            _positive_count_result(
                check_name,
                _count(connection, sql),
                "Transaction-to-Account many-to-many cardinality missing",
            )
            for check_name, sql in MANY_TO_MANY_CARDINALITY_SQL.items()
        ]


def _join_count_sql(join_path: dict[str, Any]) -> str:
    if join_path["id"] in REGISTERED_JOIN_SQL:
        return REGISTERED_JOIN_SQL[join_path["id"]]
    if len(join_path["tables"]) != 2:
        raise ValueError(f"No SQL registered for complex path {join_path['id']}")
    parent_table, child_table = join_path["tables"]
    join_keyword = (
        "INNER JOIN" if join_path["join_type"].startswith("inner") else "LEFT JOIN"
    )
    return f"""
        SELECT COUNT(*)
        FROM {parent_table}
        {join_keyword} {child_table}
            ON {join_path["join_condition"]}
    """


def _unmatched_count_sql(join_path: dict[str, Any]) -> str:
    if join_path["id"] in UNMATCHED_COUNT_SQL:
        return UNMATCHED_COUNT_SQL[join_path["id"]]
    if len(join_path["tables"]) != 2:
        raise ValueError("Unmatched-parent checks require a registered SQL query")
    parent_table, child_table = join_path["tables"]
    return f"""
        SELECT COUNT(*)
        FROM {parent_table}
        LEFT JOIN {child_table}
            ON {join_path["join_condition"]}
        WHERE {join_path["unmatched_condition"]}
    """


def _positive_count_result(
    check_name: str,
    count: int,
    zero_message: str,
) -> IntegrityCheckResult:
    if count > 0:
        return passed(check_name, f"{count} qualifying row(s)")
    return failed(check_name, zero_message)


def _zero_count_result(
    check_name: str,
    count: int,
    nonzero_message: str,
) -> IntegrityCheckResult:
    if count == 0:
        return passed(check_name, "zero invalid rows")
    return failed(check_name, f"{nonzero_message}: {count} invalid row(s)")


def _count(connection: Any, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating Finance join paths."
        ) from exc
    return duckdb


__all__ = [
    "FX_LOOKUP_INVALID_SQL",
    "FinanceJoinPathValidator",
    "MANY_TO_MANY_CARDINALITY_SQL",
    "MATCHED_COUNT_SQL",
    "REGISTERED_JOIN_SQL",
    "UNMATCHED_COUNT_SQL",
]
