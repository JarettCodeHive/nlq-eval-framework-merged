"""CRM imperfection-rate validation."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.imperfections import _near_duplicate_email
from generators.crm.imperfections import _near_duplicate_first_name
from generators.crm.validators.relational import CRMRelationalValidator


NULL_RATE_TARGETS = {
    "contact_campaigns.attribution_weight": (
        "contact_campaigns",
        "attribution_weight",
    ),
}


class CRMImperfectionRateValidator:
    """Validate CRM controlled imperfection rates against fixed config."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError(
                "CRMImperfectionRateValidator only supports the crm domain"
            )
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.crm_config = load_crm_config()

    @classmethod
    def for_profile(cls, profile: str) -> "CRMImperfectionRateValidator":
        """Create a CRM imperfection-rate validator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate imperfection rates in CSVs from the configured output path."""

        tables = self._read_csv_tables(self._csv_paths(self.settings.output_path))
        return self.validate_tables(tables)

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate imperfect CRM tables and validate configured rates."""

        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        relational_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(relational_results)
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate actual CRM tables against configured imperfection counts."""

        results: list[IntegrityCheckResult] = []
        results.extend(self._validate_duplicate_contacts(tables))
        results.extend(self._validate_null_rates(tables))
        results.extend(self._validate_outlier_rates(tables))
        results.extend(self._validate_boundary_values(tables))
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSV rates and raise on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def expected_duplicate_count(self) -> int:
        """Return the expected number of appended near-duplicate contacts."""

        return count_from_pct(
            self.generator.row_count("contacts"),
            float(self.config["duplicate_pct"]),
        )

    def expected_null_counts(self) -> dict[str, int]:
        """Return expected NULL counts for controlled NULL targets only."""

        null_pct = float(self.config["null_pct"])
        return {
            label: count_from_pct(self.generator.row_count(table_name), null_pct)
            for label, (table_name, _column_name) in NULL_RATE_TARGETS.items()
        }

    def expected_outlier_count(self) -> int:
        """Return the expected number of engagement-point outliers."""

        return count_from_pct(
            self.generator.row_count("interactions"),
            float(self.config["outlier_pct"]),
        )

    def _validate_duplicate_contacts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        base_contact_count = self.generator.row_count("contacts")
        actual_duplicate_count = len(tables["contacts"]) - base_contact_count
        expected_duplicate_count = self.expected_duplicate_count()
        result = _exact_count_result(
            "contacts.near_duplicate_rate",
            actual_duplicate_count,
            expected_duplicate_count,
        )

        base_rows = tables["contacts"].iloc[:base_contact_count]
        duplicate_rows = tables["contacts"].iloc[base_contact_count:]
        expected_variations = {
            (
                _near_duplicate_first_name(row.first_name),
                _near_duplicate_email(row.email),
            )
            for row in base_rows[["first_name", "email"]].itertuples(index=False)
        }
        actual_variations = sum(
            _is_valid_near_duplicate(row, expected_variations)
            for row in duplicate_rows[["first_name", "email"]].itertuples(index=False)
        )
        variation_result = _exact_count_result(
            "contacts.near_duplicate_variations",
            actual_variations,
            expected_duplicate_count,
        )
        return [result, variation_result]

    def _validate_null_rates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected_counts = self.expected_null_counts()
        return [
            _exact_count_result(
                f"{label}.null_rate",
                empty_count(tables[table_name][column_name]),
                expected_counts[label],
            )
            for label, (table_name, column_name) in NULL_RATE_TARGETS.items()
        ]

    def _validate_outlier_rates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.crm_config["imperfection_targets"]["engagement_point_outliers"]
        minimum = int(target["minimum_value"])
        maximum = int(target["maximum_value"])
        points = tables["interactions"]["engagement_points"].astype(int)
        outlier_points = points[points.ge(minimum)]
        count_result = _exact_count_result(
            "interactions.engagement_points.outlier_rate",
            len(outlier_points),
            self.expected_outlier_count(),
        )
        if outlier_points.empty or outlier_points.between(minimum, maximum).all():
            range_result = passed(
                "interactions.engagement_points.outlier_range",
                f"all outliers are between {minimum} and {maximum}",
            )
        else:
            range_result = failed(
                "interactions.engagement_points.outlier_range",
                f"outliers must be between {minimum} and {maximum}",
            )
        return [count_result, range_result]

    def _validate_boundary_values(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected_openings = {
            f"{value}T00:00:00" for value in self.config["boundary_dates"]
        }
        support_cases = tables["support_cases"]
        opening_text = support_cases["opened_at"].astype(str)
        actual_openings = set(opening_text)
        missing = sorted(expected_openings - actual_openings)
        if missing:
            presence_result = failed(
                "support_cases.opened_at.boundary_values",
                f"missing boundary values: {missing}",
            )
        else:
            presence_result = passed(
                "support_cases.opened_at.boundary_values",
                f"{len(expected_openings)} boundary value(s) present",
            )

        boundary_rows = support_cases[opening_text.isin(expected_openings)]
        sla_hours = self.crm_config["business_mappings"]["sla_calendar_hours"]
        resolved_statuses = set(
            self.crm_config["business_mappings"]["resolved_case_statuses"]
        )
        invalid_sla_rows = 0
        invalid_resolution_rows = 0
        for row in boundary_rows.itertuples(index=False):
            opened = _timestamp(row.opened_at)
            expected_sla = opened + timedelta(hours=int(sla_hours[row.priority]))
            if _optional_timestamp(row.sla_due_at) != expected_sla:
                invalid_sla_rows += 1

            resolution = _optional_timestamp(row.resolved_at)
            should_be_resolved = row.status in resolved_statuses
            if should_be_resolved != (resolution is not None):
                invalid_resolution_rows += 1
            elif resolution is not None and resolution < opened:
                invalid_resolution_rows += 1

        sla_result = _zero_count_result(
            "support_cases.boundary_sla_derivation",
            invalid_sla_rows,
            "boundary row(s) have invalid SLA deadlines",
        )
        resolution_result = _zero_count_result(
            "support_cases.boundary_resolution_derivation",
            invalid_resolution_rows,
            "boundary row(s) have invalid resolution timestamps",
        )
        return [presence_result, sla_result, resolution_result]

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

    def _read_csv_tables(self, csv_paths: dict[str, Path]) -> dict[str, Any]:
        pd = _require_pandas()
        return {
            table_name: pd.read_csv(
                csv_paths[table_name],
                keep_default_na=False,
                dtype=str,
            )
            for table_name in self.settings.table_order
        }


def _exact_count_result(
    check_name: str,
    actual_count: int,
    expected_count: int,
) -> IntegrityCheckResult:
    if actual_count == expected_count:
        return passed(check_name, f"{actual_count} row(s)")
    return failed(check_name, f"expected {expected_count}, got {actual_count}")


def _zero_count_result(
    check_name: str,
    invalid_count: int,
    message: str,
) -> IntegrityCheckResult:
    if invalid_count == 0:
        return passed(check_name, "all boundary rows are valid")
    return failed(check_name, f"{invalid_count} {message}")


def _is_valid_near_duplicate(
    duplicate_row: Any,
    expected_variations: set[tuple[str, str]],
) -> bool:
    """Return whether a row matches a valid generated name/email variation."""

    variation = (str(duplicate_row.first_name), str(duplicate_row.email))
    return variation in expected_variations


def _timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_timestamp(value: object) -> datetime | None:
    text = str(value)
    return _timestamp(text) if text else None


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Create the root .venv and "
            "install requirements.txt before validating imperfection rates."
        ) from exc
    return pd
