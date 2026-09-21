"""DuckDB DDL, foreign-key, uniqueness, and accounting validation for Finance."""

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
from generators.finance.config import settings_for_profile
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.relational import FinanceRelationalValidator


def _duplicate_count_sql(table_name: str, fields: list[str]) -> str:
    field_list = ", ".join(fields)
    return f"""
        SELECT COUNT(*)
        FROM (
            SELECT {field_list}
            FROM {table_name}
            GROUP BY {field_list}
            HAVING COUNT(*) > 1
        ) AS duplicate_keys
    """


MANUAL_FK_CHECKS = {
    "accounts.parent_account_id.fk": """
        SELECT COUNT(*)
        FROM accounts AS child
        LEFT JOIN accounts AS parent
            ON child.parent_account_id = parent.account_id
        WHERE child.parent_account_id IS NOT NULL
          AND parent.account_id IS NULL
    """,
    "ledger_entries.transaction_id.fk": """
        SELECT COUNT(*)
        FROM ledger_entries
        LEFT JOIN transactions
            ON ledger_entries.transaction_id = transactions.transaction_id
        WHERE transactions.transaction_id IS NULL
    """,
    "ledger_entries.account_id.fk": """
        SELECT COUNT(*)
        FROM ledger_entries
        LEFT JOIN accounts
            ON ledger_entries.account_id = accounts.account_id
        WHERE accounts.account_id IS NULL
    """,
    "budgets.account_id.fk": """
        SELECT COUNT(*)
        FROM budgets
        LEFT JOIN accounts
            ON budgets.account_id = accounts.account_id
        WHERE accounts.account_id IS NULL
    """,
}


UNIQUE_KEY_CHECKS = {
    "accounts.account_id.unique": _duplicate_count_sql("accounts", ["account_id"]),
    "accounts.account_number.unique": _duplicate_count_sql(
        "accounts", ["account_number"]
    ),
    "transactions.transaction_id.unique": _duplicate_count_sql(
        "transactions", ["transaction_id"]
    ),
    "ledger_entries.entry_id.unique": _duplicate_count_sql(
        "ledger_entries", ["entry_id"]
    ),
    "ledger_entries.transaction_line.unique": _duplicate_count_sql(
        "ledger_entries", ["transaction_id", "line_number"]
    ),
    "budgets.budget_id.unique": _duplicate_count_sql("budgets", ["budget_id"]),
    "fx_rates.rate_id.unique": _duplicate_count_sql("fx_rates", ["rate_id"]),
    "fx_rates.currency_date.unique": _duplicate_count_sql(
        "fx_rates", ["from_currency", "to_currency", "rate_date"]
    ),
}


ACCOUNTING_CHECKS = {
    "ledger_entries.debit_credit_exclusivity": """
        SELECT COUNT(*)
        FROM ledger_entries
        WHERE (debit_amount IS NULL) = (credit_amount IS NULL)
    """,
    "ledger_entries.transaction_balance": """
        SELECT COUNT(*)
        FROM (
            SELECT transaction_id
            FROM ledger_entries
            GROUP BY transaction_id
            HAVING COALESCE(SUM(debit_amount), 0)
                <> COALESCE(SUM(credit_amount), 0)
        ) AS unbalanced
    """,
    "ledger_entries.transaction_amount_alignment": """
        SELECT COUNT(*)
        FROM (
            SELECT
                ledger_entries.transaction_id,
                transactions.total_amount
            FROM ledger_entries
            INNER JOIN transactions
                ON ledger_entries.transaction_id = transactions.transaction_id
            GROUP BY
                ledger_entries.transaction_id,
                transactions.total_amount
            HAVING COALESCE(SUM(ledger_entries.debit_amount), 0)
                    <> transactions.total_amount
                OR COALESCE(SUM(ledger_entries.credit_amount), 0)
                    <> transactions.total_amount
        ) AS misaligned
    """,
    "ledger_entries.transaction_currency": """
        SELECT COUNT(*)
        FROM ledger_entries
        INNER JOIN transactions
            ON ledger_entries.transaction_id = transactions.transaction_id
        WHERE ledger_entries.currency_code <> transactions.source_currency
    """,
}


class FinanceDuckDBFKValidator:
    """Validate Finance CSVs with canonical DDL and explicit SQL checks."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceDuckDBFKValidator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceDuckDBFKValidator":
        """Create a DuckDB validator from validated Finance config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSVs from the configured profile output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one complete persisted Finance CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final Finance tables and validate temporary CSV exports."""

        tables = FinanceImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        FinanceRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="finance-fk-validation-") as temp_dir:
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

            results.extend(_execute_zero_count_checks(connection, MANUAL_FK_CHECKS))
            results.extend(_execute_zero_count_checks(connection, UNIQUE_KEY_CHECKS))
            results.extend(_execute_zero_count_checks(connection, ACCOUNTING_CHECKS))
        return results


def _execute_zero_count_checks(
    connection: Any,
    checks: dict[str, str],
) -> list[IntegrityCheckResult]:
    results: list[IntegrityCheckResult] = []
    for check_name, sql in checks.items():
        try:
            invalid_count = int(connection.execute(sql).fetchone()[0])
        except Exception as exc:
            results.append(failed(check_name, f"check query failed: {exc}"))
            continue
        if invalid_count == 0:
            results.append(passed(check_name, "zero invalid rows"))
        else:
            results.append(failed(check_name, f"{invalid_count} invalid row(s)"))
    return results


def load_finance_csvs(
    connection: Any,
    csv_paths: dict[str, Path],
    table_order: tuple[str, ...],
) -> list[IntegrityCheckResult]:
    """Load Finance CSVs under DDL constraints in dependency-safe order."""

    results: list[IntegrityCheckResult] = []
    for table_name in table_order:
        try:
            if table_name == "accounts":
                _load_self_referencing_accounts(connection, csv_paths[table_name])
            else:
                connection.execute(_copy_sql(table_name, csv_paths[table_name]))
        except Exception as exc:
            results.append(
                failed(
                    f"{table_name}.duckdb_load",
                    f"failed to load CSV into constrained table: {exc}",
                )
            )
            return results
        results.append(
            passed(
                f"{table_name}.duckdb_load",
                "loaded under canonical DDL constraints",
            )
        )
    return results


def _load_self_referencing_accounts(connection: Any, path: Path) -> None:
    """Load account roots before descendants so DuckDB can enforce the self-FK."""

    staging = "accounts_staging"
    connection.execute(
        f"CREATE TEMP TABLE {staging} AS SELECT * FROM accounts WHERE FALSE"
    )
    connection.execute(_copy_sql(staging, path))
    connection.execute(
        f"""
        INSERT INTO accounts
        SELECT * FROM {staging}
        WHERE parent_account_id IS NULL
        """
    )

    total = int(connection.execute(f"SELECT COUNT(*) FROM {staging}").fetchone()[0])
    loaded = int(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
    while loaded < total:
        connection.execute(
            f"""
            INSERT INTO accounts
            SELECT staging.*
            FROM {staging} AS staging
            INNER JOIN accounts AS parent
                ON staging.parent_account_id = parent.account_id
            LEFT JOIN accounts AS existing
                ON staging.account_id = existing.account_id
            WHERE existing.account_id IS NULL
            """
        )
        new_loaded = int(
            connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        )
        if new_loaded == loaded:
            unresolved = total - loaded
            raise ValueError(
                f"{unresolved} account row(s) have unresolved parent references"
            )
        loaded = new_loaded
    connection.execute(f"DROP TABLE {staging}")


def _copy_sql(table_name: str, path: Path) -> str:
    return f"""
        COPY {table_name}
        FROM {_sql_string(path)}
        (HEADER, DELIMITER ',', NULL '', DATEFORMAT '%Y-%m-%d',
         TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')
    """


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating Finance FK integrity."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"


__all__ = [
    "ACCOUNTING_CHECKS",
    "FinanceDuckDBFKValidator",
    "MANUAL_FK_CHECKS",
    "UNIQUE_KEY_CHECKS",
    "load_finance_csvs",
]
