"""Sales imperfection-rate validation for generated and exported datasets."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.relational import SalesRelationalValidator


class SalesImperfectionRateValidator:
    """Validate observable Sales imperfections against deterministic config."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesImperfectionRateValidator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.sales_config = load_sales_config()

    @classmethod
    def for_profile(cls, profile: str) -> "SalesImperfectionRateValidator":
        """Create a Sales rate validator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate rates in CSVs from the configured profile output path."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate imperfections in one complete persisted Sales CSV directory."""

        tables = self._read_csv_tables(self._csv_paths(directory))
        return self.validate_tables(tables)

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final Sales tables and validate their imperfection rates."""

        tables = SalesImperfectionInjector(self.generator).generate_imperfect_tables()
        SalesRelationalValidator(self.generator).validate_or_raise(tables)
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return independent results for every configured imperfection target."""

        results: list[IntegrityCheckResult] = []
        results.extend(self._validate_duplicate_quotations(tables))
        results.append(self._validate_missing_product_prices(tables))
        results.extend(self._validate_deal_outliers(tables))
        results.extend(self._validate_boundary_timestamps(tables))
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSV rates and raise one combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def expected_duplicate_count(self) -> int:
        """Return the expected number of near-duplicate quotation rows."""

        return count_from_pct(
            self.generator.row_count("quotations"),
            float(self.config["duplicate_pct"]),
        )

    def expected_null_count(self) -> int:
        """Return the expected number of missing product list prices."""

        return count_from_pct(
            self.generator.row_count("products"),
            float(self.config["null_pct"]),
        )

    def expected_outlier_count(self) -> int:
        """Return the expected number of deal-amount outliers."""

        return count_from_pct(
            self.generator.row_count("deals"),
            float(self.config["outlier_pct"]),
        )

    def _validate_duplicate_quotations(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        quotations = tables["quotations"]
        target = self.sales_config["imperfection_targets"][
            "near_duplicate_quotation_lines"
        ]
        business_keys = target["business_key_fields"]
        variation_fields = set(target["variation_fields"])
        duplicate_groups = quotations.groupby(
            business_keys,
            dropna=False,
            sort=False,
        )

        duplicate_count = 0
        valid_variation_count = 0
        for _, group in duplicate_groups:
            if len(group) == 1:
                continue
            duplicate_count += len(group) - 1
            if _is_valid_quotation_duplicate_group(
                group,
                business_keys=set(business_keys),
                variation_fields=variation_fields,
            ):
                valid_variation_count += len(group) - 1

        expected = self.expected_duplicate_count()
        return [
            _exact_count_result(
                "quotations.near_duplicate_rate",
                duplicate_count,
                expected,
            ),
            _exact_count_result(
                "quotations.near_duplicate_variations",
                valid_variation_count,
                expected,
            ),
        ]

    def _validate_missing_product_prices(
        self,
        tables: dict[str, Any],
    ) -> IntegrityCheckResult:
        return _exact_count_result(
            "products.list_price.null_rate",
            empty_count(tables["products"]["list_price"]),
            self.expected_null_count(),
        )

    def _validate_deal_outliers(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.sales_config["imperfection_targets"]["deal_amount_outliers"]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        scale = int(target["scale"])
        outliers: list[Decimal] = []
        invalid_decimals = 0
        for value in tables["deals"]["deal_amount"]:
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                invalid_decimals += 1
                continue
            if amount >= minimum:
                outliers.append(amount)

        count_result = _exact_count_result(
            "deals.deal_amount.outlier_rate",
            len(outliers),
            self.expected_outlier_count(),
        )
        values_in_range = invalid_decimals == 0 and all(
            minimum <= value <= maximum for value in outliers
        )
        range_result = _condition_result(
            "deals.deal_amount.outlier_range",
            values_in_range,
            f"all outliers are between {minimum} and {maximum}",
            f"{invalid_decimals} invalid decimal(s) or outlier outside range",
        )
        correct_scale = invalid_decimals == 0 and all(
            value.as_tuple().exponent == -scale for value in outliers
        )
        scale_result = _condition_result(
            "deals.deal_amount.outlier_scale",
            correct_scale,
            f"all outliers use scale {scale}",
            f"outliers must use scale {scale}",
        )
        return [count_result, range_result, scale_result]

    def _validate_boundary_timestamps(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.sales_config["imperfection_targets"][
            "product_created_boundary_timestamps"
        ]
        expected_values = [
            f"{value}T{target['timestamp_time']}"
            for value in self.config["boundary_dates"]
        ]
        actual = tables["products"]["created_at"].astype(str)
        counts = {value: int(actual.eq(value).sum()) for value in expected_values}
        missing = [value for value, count in counts.items() if count == 0]
        presence_result = _condition_result(
            "products.created_at.boundary_values",
            not missing,
            f"all {len(expected_values)} boundary values are present",
            f"missing boundary values: {missing}",
        )
        occurrence_count = sum(counts.values())
        occurrence_result = _exact_count_result(
            "products.created_at.boundary_occurrences",
            occurrence_count,
            len(expected_values),
        )
        return [presence_result, occurrence_result]

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Sales CSVs are missing. Export the requested profile first or "
                "use generated validation. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        return paths

    def _read_csv_tables(self, paths: dict[str, Path]) -> dict[str, Any]:
        pd = _require_pandas()
        return {
            table_name: pd.read_csv(
                paths[table_name],
                keep_default_na=False,
                dtype=str,
            )
            for table_name in self.settings.table_order
        }


def _is_valid_quotation_duplicate_group(
    group: Any,
    business_keys: set[str],
    variation_fields: set[str],
) -> bool:
    """Check a duplicate group using only documented keys and variations."""

    # One injected duplicate is permitted for each unique base business key.
    if len(group) != 2:
        return False
    ignored_fields = business_keys | variation_fields | {"quotation_id"}
    stable_fields = [field for field in group.columns if field not in ignored_fields]
    first, second = group.iloc[0], group.iloc[1]
    stable_match = all(
        str(first[field]) == str(second[field]) for field in stable_fields
    )
    has_permitted_variation = any(
        str(first[field]) != str(second[field]) for field in variation_fields
    )
    return stable_match and has_permitted_variation


def _exact_count_result(
    check_name: str,
    actual_count: int,
    expected_count: int,
) -> IntegrityCheckResult:
    if actual_count == expected_count:
        return passed(check_name, f"{actual_count} row(s)")
    return failed(check_name, f"expected {expected_count}, got {actual_count}")


def _condition_result(
    check_name: str,
    condition: bool,
    success_message: str,
    failure_message: str,
) -> IntegrityCheckResult:
    if condition:
        return passed(check_name, success_message)
    return failed(check_name, failure_message)


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Create the root .venv and "
            "install requirements.txt before validating Sales imperfection rates."
        ) from exc
    return pd


__all__ = ["SalesImperfectionRateValidator"]
