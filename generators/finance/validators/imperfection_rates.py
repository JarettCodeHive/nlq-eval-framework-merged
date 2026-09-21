"""Finance imperfection-rate validation for generated and exported datasets."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.imperfect_tables import (
    IMPERFECTION_MUTABLE_FIELDS,
)
from generators.finance.validators.relational import FinanceRelationalValidator


class FinanceImperfectionRateValidator:
    """Validate observable Finance imperfections against deterministic config."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceImperfectionRateValidator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.finance_config = load_finance_config()

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceImperfectionRateValidator":
        """Create a Finance rate validator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate configured final CSVs against a regenerated distributed stage."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one persisted directory against a clean distributed stage."""

        tables = self._read_csv_tables(self._csv_paths(directory))
        clean_generator = DeterministicGenerator(self.settings)
        source_tables = FinanceDistributionApplier(
            clean_generator
        ).generate_distributed_tables()
        return self.validate_tables(tables, source_tables=source_tables)

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate distributed and imperfect Finance stages, then validate rates."""

        source_tables = FinanceDistributionApplier(
            self.generator
        ).generate_distributed_tables()
        tables = FinanceImperfectionInjector(self.generator).apply_to_tables(
            source_tables
        )
        return self.validate_tables(tables, source_tables=source_tables)

    def validate_tables(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> list[IntegrityCheckResult]:
        """Return independent results for every Finance imperfection contract."""

        if source_tables is None:
            source_tables = FinanceDistributionApplier(
                self.generator
            ).generate_distributed_tables()

        results: list[IntegrityCheckResult] = []
        results.extend(self._validate_missing_fx_rates(tables))
        results.extend(self._validate_duplicate_budgets(tables))
        results.extend(self._validate_transaction_outliers(tables))
        results.extend(self._validate_boundary_dates(tables))
        results.extend(self._validate_imperfection_scope(tables, source_tables))
        results.append(self._validate_relational_invariants(tables))
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSV rates and raise one combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def expected_null_count(self) -> int:
        """Return the expected number of missing FX rates."""

        return count_from_pct(
            self.generator.row_count("fx_rates"),
            float(self.config["null_pct"]),
        )

    def expected_duplicate_count(self) -> int:
        """Return the expected number of near-duplicate budget rows."""

        return count_from_pct(
            self.generator.row_count("budgets"),
            float(self.config["duplicate_pct"]),
        )

    def expected_outlier_count(self) -> int:
        """Return the expected number of transaction amount outliers."""

        return count_from_pct(
            self.generator.row_count("transactions"),
            float(self.config["outlier_pct"]),
        )

    def _validate_missing_fx_rates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        fx_rates = tables["fx_rates"]
        missing = fx_rates[fx_rates["rate"].astype(str).eq("")]
        target = self.finance_config["imperfection_targets"]["missing_fx_rates"]
        protected_from, protected_to = str(target["protected_pair"]).split("/", 1)
        protected_missing = int(
            (
                missing["from_currency"].eq(protected_from)
                & missing["to_currency"].eq(protected_to)
            ).sum()
        )
        boundary_missing = int(
            missing["rate_date"].isin(self.config["boundary_dates"]).sum()
        )
        missing_keys = set(
            missing[["from_currency", "to_currency", "rate_date"]].itertuples(
                index=False,
                name=None,
            )
        )
        transaction_keys = set(
            tables["transactions"][
                ["source_currency", "target_currency", "transaction_date"]
            ].itertuples(index=False, name=None)
        )
        return [
            _exact_count_result(
                "fx_rates.rate.null_rate",
                len(missing),
                self.expected_null_count(),
            ),
            _zero_count_result(
                "fx_rates.rate.protected_pair",
                protected_missing,
                "protected identity FX row(s) contain NULL rates",
            ),
            _zero_count_result(
                "fx_rates.rate.protected_boundaries",
                boundary_missing,
                "boundary FX row(s) contain NULL rates",
            ),
            _condition_result(
                "fx_rates.rate.queryable_missing_path",
                bool(missing_keys & transaction_keys),
                "at least one transaction exercises a missing FX rate",
                "missing FX rates are not referenced by any transaction",
            ),
        ]

    def _validate_duplicate_budgets(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        budgets = tables["budgets"]
        target = self.finance_config["imperfection_targets"][
            "near_duplicate_budgets"
        ]
        business_keys = target["business_key_fields"]
        variation_fields = set(target["variation_fields"])
        groups = budgets.groupby(business_keys, dropna=False, sort=False)

        duplicate_count = 0
        valid_key_count = 0
        valid_variation_count = 0
        for _, group in groups:
            if len(group) == 1:
                continue
            additional_rows = len(group) - 1
            duplicate_count += additional_rows
            if len(group) == 2:
                valid_key_count += additional_rows
            if _is_valid_budget_duplicate_group(
                group,
                business_keys=set(business_keys),
                variation_fields=variation_fields,
            ):
                valid_variation_count += additional_rows

        expected = self.expected_duplicate_count()
        return [
            _exact_count_result(
                "budgets.near_duplicate_rate",
                duplicate_count,
                expected,
            ),
            _exact_count_result(
                "budgets.near_duplicate_business_keys",
                valid_key_count,
                expected,
            ),
            _exact_count_result(
                "budgets.near_duplicate_variations",
                valid_variation_count,
                expected,
            ),
        ]

    def _validate_transaction_outliers(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.finance_config["imperfection_targets"][
            "transaction_amount_outliers"
        ]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        normal_maximum = Decimal(
            str(self.settings.distributions["transaction_amount"]["max_amount"])
        )
        scale = int(target["scale"])
        outliers: list[Decimal] = []
        invalid_decimals = 0
        invalid_range = 0
        for value in tables["transactions"]["total_amount"]:
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                invalid_decimals += 1
                continue
            if amount >= minimum:
                outliers.append(amount)
            elif amount > normal_maximum:
                invalid_range += 1

        return [
            _exact_count_result(
                "transactions.total_amount.outlier_rate",
                len(outliers),
                self.expected_outlier_count(),
            ),
            _condition_result(
                "transactions.total_amount.outlier_range",
                invalid_decimals == 0
                and invalid_range == 0
                and all(minimum <= value <= maximum for value in outliers),
                f"all outliers are between {minimum} and {maximum}",
                f"{invalid_decimals} invalid decimal(s), {invalid_range} amount(s) "
                "between normal and outlier ranges, or outlier outside range",
            ),
            _condition_result(
                "transactions.total_amount.outlier_scale",
                invalid_decimals == 0
                and all(value.as_tuple().exponent == -scale for value in outliers),
                f"all outliers use scale {scale}",
                f"outliers must use scale {scale}",
            ),
        ]

    def _validate_boundary_dates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = list(self.config["boundary_dates"])
        actual = tables["transactions"]["transaction_date"].astype(str)
        counts = {value: int(actual.eq(value).sum()) for value in expected}
        missing = [value for value, count in counts.items() if count == 0]
        return [
            _condition_result(
                "transactions.transaction_date.boundary_values",
                not missing,
                f"all {len(expected)} boundary values are present",
                f"missing boundary values: {missing}",
            ),
            _exact_count_result(
                "transactions.transaction_date.boundary_occurrences",
                sum(counts.values()),
                len(expected),
            ),
        ]

    def _validate_imperfection_scope(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        contract_errors = 0
        field_errors = 0
        expected_tables = self.settings.table_order
        if tuple(tables) != expected_tables or tuple(source_tables) != expected_tables:
            contract_errors += 1

        for table_name in expected_tables:
            if table_name not in tables or table_name not in source_tables:
                contract_errors += 1
                continue
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].reset_index(drop=True)
            if result.columns.tolist() != source.columns.tolist():
                contract_errors += 1
                continue
            expected_rows = len(source) + (
                self.expected_duplicate_count() if table_name == "budgets" else 0
            )
            if len(result) != expected_rows:
                contract_errors += 1
                continue
            result_base = result.iloc[: len(source)].reset_index(drop=True)
            for column_name in source.columns:
                if column_name in IMPERFECTION_MUTABLE_FIELDS[table_name]:
                    continue
                if _normalized_values(source[column_name]) != _normalized_values(
                    result_base[column_name]
                ):
                    field_errors += 1

        return [
            _zero_count_result(
                "finance.imperfection_scope.table_contract",
                contract_errors,
                "table contract violation(s)",
            ),
            _zero_count_result(
                "finance.imperfection_scope.unapproved_fields",
                field_errors,
                "unapproved field modification(s)",
            ),
        ]

    def _validate_relational_invariants(
        self,
        tables: dict[str, Any],
    ) -> IntegrityCheckResult:
        results = FinanceRelationalValidator(self.generator).validate_tables(tables)
        failures = [result.check_name for result in results if not result.passed]
        return _condition_result(
            "finance.accounting_fx_invariants",
            not failures,
            f"all {len(results)} relational, accounting, and FX checks passed",
            f"failed checks: {failures[:10]}",
        )

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

    def _read_csv_tables(self, paths: dict[str, Path]) -> dict[str, Any]:
        pd = _require_pandas()
        tables: dict[str, Any] = {}
        for table_name in self.settings.table_order:
            table = pd.read_csv(
                paths[table_name],
                keep_default_na=False,
                dtype=str,
            )
            fields = self.finance_config["tables"][table_name]["fields"]
            for field in fields:
                column_name = field["name"]
                if field["type"] == "integer":
                    table[column_name] = table[column_name].map(
                        lambda value: "" if value == "" else int(value)
                    )
                elif field["type"] == "boolean":
                    table[column_name] = table[column_name].map(
                        lambda value: str(value).lower() == "true"
                    )
            tables[table_name] = table
        return tables


def _is_valid_budget_duplicate_group(
    group: Any,
    business_keys: set[str],
    variation_fields: set[str],
) -> bool:
    """Check that one budget duplicate changes only approved variation fields."""

    if len(group) != 2:
        return False
    ignored_fields = business_keys | variation_fields | {"budget_id"}
    stable_fields = [field for field in group.columns if field not in ignored_fields]
    first, second = group.iloc[0], group.iloc[1]
    stable_match = all(
        str(first[field]) == str(second[field]) for field in stable_fields
    )
    has_permitted_variation = any(
        str(first[field]) != str(second[field]) for field in variation_fields
    )
    return stable_match and has_permitted_variation


def _normalized_values(series: Any) -> list[str]:
    return [_normalized_value(value) for value in series.tolist()]


def _normalized_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None or value != value:
        return ""
    return str(value)


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
        return passed(check_name, "zero invalid rows")
    return failed(check_name, f"{invalid_count} {message}")


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
            "install requirements.txt before validating Finance imperfection rates."
        ) from exc
    return pd


__all__ = ["FinanceImperfectionRateValidator"]
