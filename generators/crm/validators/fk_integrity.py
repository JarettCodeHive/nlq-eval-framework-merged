"""DuckDB constraint and foreign-key validation for CRM CSVs."""

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
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.validators.relational import CRMRelationalValidator


MANUAL_FK_CHECKS = {
    "contacts.account_id.fk": """
        SELECT COUNT(*)
        FROM contacts
        LEFT JOIN accounts
            ON contacts.account_id = accounts.account_id
        WHERE contacts.account_id IS NOT NULL
          AND accounts.account_id IS NULL
    """,
    "contact_campaigns.contact_id.fk": """
        SELECT COUNT(*)
        FROM contact_campaigns
        LEFT JOIN contacts
            ON contact_campaigns.contact_id = contacts.contact_id
        WHERE contacts.contact_id IS NULL
    """,
    "contact_campaigns.campaign_id.fk": """
        SELECT COUNT(*)
        FROM contact_campaigns
        LEFT JOIN campaigns
            ON contact_campaigns.campaign_id = campaigns.campaign_id
        WHERE campaigns.campaign_id IS NULL
    """,
    "interactions.contact_id.fk": """
        SELECT COUNT(*)
        FROM interactions
        LEFT JOIN contacts
            ON interactions.contact_id = contacts.contact_id
        WHERE contacts.contact_id IS NULL
    """,
    "interactions.account_id.fk": """
        SELECT COUNT(*)
        FROM interactions
        LEFT JOIN accounts
            ON interactions.account_id = accounts.account_id
        WHERE interactions.account_id IS NOT NULL
          AND accounts.account_id IS NULL
    """,
    "interactions.campaign_id.fk": """
        SELECT COUNT(*)
        FROM interactions
        LEFT JOIN campaigns
            ON interactions.campaign_id = campaigns.campaign_id
        WHERE interactions.campaign_id IS NOT NULL
          AND campaigns.campaign_id IS NULL
    """,
    "support_cases.account_id.fk": """
        SELECT COUNT(*)
        FROM support_cases
        LEFT JOIN accounts
            ON support_cases.account_id = accounts.account_id
        WHERE accounts.account_id IS NULL
    """,
    "support_cases.contact_id.fk": """
        SELECT COUNT(*)
        FROM support_cases
        LEFT JOIN contacts
            ON support_cases.contact_id = contacts.contact_id
        WHERE support_cases.contact_id IS NOT NULL
          AND contacts.contact_id IS NULL
    """,
}


SEMANTIC_RELATIONSHIP_CHECKS = {
    "contact_campaigns.composite_pk": """
        SELECT COUNT(*)
        FROM (
            SELECT contact_id, campaign_id
            FROM contact_campaigns
            GROUP BY contact_id, campaign_id
            HAVING COUNT(*) > 1
        ) AS duplicate_pairs
    """,
    "interactions.contact_account_consistency": """
        SELECT COUNT(*)
        FROM interactions
        INNER JOIN contacts
            ON interactions.contact_id = contacts.contact_id
        WHERE interactions.account_id IS NOT NULL
          AND (
              contacts.account_id IS NULL
              OR interactions.account_id <> contacts.account_id
          )
    """,
    "interactions.contact_campaign_membership": """
        SELECT COUNT(*)
        FROM interactions
        LEFT JOIN contact_campaigns
            ON interactions.contact_id = contact_campaigns.contact_id
            AND interactions.campaign_id = contact_campaigns.campaign_id
        WHERE interactions.campaign_id IS NOT NULL
          AND contact_campaigns.contact_id IS NULL
    """,
    "support_cases.contact_account_consistency": """
        SELECT COUNT(*)
        FROM support_cases
        INNER JOIN contacts
            ON support_cases.contact_id = contacts.contact_id
        WHERE support_cases.contact_id IS NOT NULL
          AND (
              contacts.account_id IS NULL
              OR support_cases.account_id <> contacts.account_id
          )
    """,
}


class CRMDuckDBFKValidator:
    """Validate CRM CSVs through constrained loading and explicit anti-joins."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMDuckDBFKValidator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMDuckDBFKValidator":
        """Create a DuckDB validator from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSVs already exported to the configured output path."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate a complete persisted CRM CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate imperfect CRM tables and validate temporary CSV exports."""

        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        relational_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(relational_results)

        with TemporaryDirectory(prefix="crm-fk-validation-") as temp_dir:
            temp_path = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=temp_path,
            )
            return self._validate_csv_paths(self._csv_paths(temp_path))

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSVs and raise a combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        csv_paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in csv_paths.values() if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(
                "CRM CSVs are missing. Run "
                "`python main.py export-csvs --profile full` first, or use "
                "`--generated` for generated validation. Missing: "
                f"{missing_text}"
            )
        return csv_paths

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

            for table_name in self.settings.table_order:
                csv_path = csv_paths[table_name]
                try:
                    connection.execute(
                        f"""
                        COPY {table_name}
                        FROM {_sql_string(csv_path)}
                        (HEADER, DELIMITER ',', NULL '', DATEFORMAT '%Y-%m-%d',
                         TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')
                        """
                    )
                except Exception as exc:
                    results.append(
                        failed(
                            f"{table_name}.duckdb_load",
                            "failed to load CSV into constrained table: " f"{exc}",
                        )
                    )
                    return results
                results.append(
                    passed(
                        f"{table_name}.duckdb_load",
                        "loaded under DDL constraints",
                    )
                )

            results.extend(_execute_zero_count_checks(connection, MANUAL_FK_CHECKS))
            results.extend(
                _execute_zero_count_checks(connection, SEMANTIC_RELATIONSHIP_CHECKS)
            )
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


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating FK integrity."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"
