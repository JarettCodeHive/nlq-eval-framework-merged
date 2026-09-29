"""Logistics imperfection-rate validation for final datasets."""

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
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.relational import LogisticsRelationalValidator


class LogisticsImperfectionRateValidator:
    """Validate observable Logistics defects against deterministic config."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsImperfectionRateValidator only supports logistics"
            )
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.logistics_config = load_logistics_config()
        self.targets = self.logistics_config["imperfection_targets"]

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsImperfectionRateValidator":
        """Create an imperfection validator from Logistics profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate configured final CSVs against a fresh distributed stage."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one persisted directory against deterministic source data."""

        tables = self._read_csv_tables(self._csv_paths(directory))
        source = self._fresh_distributed_tables()
        return self.validate_tables(tables, source_tables=source)

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate independent source/final stages and validate defect rates."""

        source = self._fresh_distributed_tables()
        tables = LogisticsImperfectionInjector(
            DeterministicGenerator(self.settings)
        ).apply_to_tables(source)
        return self.validate_tables(tables, source_tables=source)

    def validate_tables(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> list[IntegrityCheckResult]:
        """Return independent results for every Logistics defect contract."""

        source = source_tables or self._fresh_distributed_tables()
        results: list[IntegrityCheckResult] = []
        results.extend(self._validate_duplicate_shipments(tables))
        results.extend(self._validate_warehouse_defects(tables))
        results.extend(self._validate_order_total_outliers(tables))
        results.extend(self._validate_boundary_paths(tables))
        results.extend(self._validate_scope(tables, source))
        results.append(self._validate_relational_invariants(tables))
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported rates and raise a combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def expected_duplicate_count(self) -> int:
        """Return the expected appended near-duplicate shipment rows."""

        return count_from_pct(
            self.generator.row_count("shipments"),
            float(self.config["duplicate_pct"]),
        )

    def expected_null_count(self) -> int:
        """Return the expected missing warehouse assignments."""

        return count_from_pct(
            self.generator.row_count("orders"),
            float(self.config["null_pct"]),
        )

    def expected_orphan_count(self) -> int:
        """Return the expected declared warehouse orphans."""

        return count_from_pct(
            self.generator.row_count("orders"),
            float(self.targets["orphaned_order_warehouses"]["rate_pct"]),
        )

    def expected_outlier_count(self) -> int:
        """Return the expected order-total outliers."""

        return count_from_pct(
            self.generator.row_count("orders"),
            float(self.config["outlier_pct"]),
        )

    def _validate_duplicate_shipments(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        shipments = tables["shipments"]
        target = self.targets["near_duplicate_shipments"]
        keys = target["business_key_fields"]
        variations = set(target["variation_fields"])
        duplicate_count = 0
        valid_key_count = 0
        valid_variation_count = 0
        for _, group in shipments.groupby(keys, dropna=False, sort=False):
            if len(group) == 1:
                continue
            additional = len(group) - 1
            duplicate_count += additional
            if len(group) == 2:
                valid_key_count += additional
            if _valid_duplicate_group(group, set(keys), variations):
                valid_variation_count += additional
        expected = self.expected_duplicate_count()
        return [
            _exact_count_result(
                "shipments.near_duplicate_rate", duplicate_count, expected
            ),
            _exact_count_result(
                "shipments.near_duplicate_business_keys",
                valid_key_count,
                expected,
            ),
            _exact_count_result(
                "shipments.near_duplicate_variations",
                valid_variation_count,
                expected,
            ),
        ]

    def _validate_warehouse_defects(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        orders = tables["orders"]
        warehouse_ids = set(tables["warehouses"]["warehouse_id"])
        field = self.targets["missing_order_warehouses"]["field"]
        missing = orders[orders[field].map(_is_blank)]
        orphaned = orders[
            orders[field].map(
                lambda value: not _is_blank(value) and value not in warehouse_ids
            )
        ]
        namespace = int(
            self.targets["orphaned_order_warehouses"]["namespace_base"]
        )
        formula_errors = sum(
            int(row.warehouse_id) != namespace + int(row.order_id)
            for row in orphaned.itertuples(index=False)
        )
        missing_ids = set(missing["order_id"])
        orphan_ids = set(orphaned["order_id"])
        return [
            _exact_count_result(
                "orders.warehouse_id.null_rate",
                len(missing),
                self.expected_null_count(),
            ),
            _exact_count_result(
                "orders.warehouse_id.orphan_rate",
                len(orphaned),
                self.expected_orphan_count(),
            ),
            _zero_count_result(
                "orders.warehouse_id.orphan_namespace",
                formula_errors,
                "orphan namespace/formula violation(s)",
            ),
            _condition_result(
                "orders.warehouse_id.disjoint_selections",
                missing_ids.isdisjoint(orphan_ids),
                "NULL and orphan selections are disjoint",
                "NULL and orphan selections overlap",
            ),
        ]

    def _validate_order_total_outliers(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.targets["order_total_outliers"]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        clean_maximum = Decimal(
            str(self.logistics_config["distributions"]["order_total"]["max_amount"])
        )
        scale = int(target["scale"])
        outliers: list[Decimal] = []
        invalid = 0
        gap_values = 0
        for raw in tables[target["table"]][target["field"]]:
            try:
                amount = Decimal(str(raw))
            except InvalidOperation:
                invalid += 1
                continue
            if amount >= minimum:
                outliers.append(amount)
            elif amount > clean_maximum:
                gap_values += 1
        return [
            _exact_count_result(
                "orders.total_amount.outlier_rate",
                len(outliers),
                self.expected_outlier_count(),
            ),
            _condition_result(
                "orders.total_amount.outlier_range",
                invalid == 0
                and gap_values == 0
                and all(minimum <= value <= maximum for value in outliers),
                f"all outliers are between {minimum} and {maximum}",
                "invalid decimal, gap value, or outlier outside configured range",
            ),
            _condition_result(
                "orders.total_amount.outlier_scale",
                invalid == 0
                and all(value.as_tuple().exponent == -scale for value in outliers),
                f"all outliers use scale {scale}",
                f"outliers must use scale {scale}",
            ),
        ]

    def _validate_boundary_paths(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        boundaries = set(self.config["boundary_dates"])
        target = self.targets["coordinated_boundary_dates"]
        results: list[IntegrityCheckResult] = []
        for qualified in [target["primary_target"], *target["dependent_targets"]]:
            table_name, field_name = qualified.split(".", 1)
            values = [
                str(value).split("T", 1)[0]
                for value in tables[table_name][field_name]
                if not _is_blank(value)
            ]
            missing = sorted(boundaries - set(values))
            results.append(
                _condition_result(
                    f"{qualified}.boundary_values",
                    not missing,
                    f"all {len(boundaries)} boundary values are present",
                    f"missing boundary values: {missing}",
                )
            )

        primary_table, primary_field = target["primary_target"].split(".", 1)
        occurrences = sum(
            str(value) in boundaries
            for value in tables[primary_table][primary_field]
        )
        results.append(
            _exact_count_result(
                f"{target['primary_target']}.boundary_occurrences",
                occurrences,
                len(boundaries),
            )
        )

        orders = tables["orders"]
        shipments = tables["shipments"]
        valid_paths = 0
        for boundary in boundaries:
            boundary_orders = orders[orders["order_date"] == boundary]
            if len(boundary_orders) != 1:
                continue
            order_id = boundary_orders.iloc[0]["order_id"]
            path = shipments[
                (shipments["order_id"] == order_id)
                & (shipments["status"] == "Delivered")
                & (shipments["ship_date"] == boundary)
                & (shipments["delivery_date"] == boundary)
            ]
            valid_paths += not path.empty
        results.append(
            _exact_count_result(
                "logistics.coordinated_boundary_paths",
                valid_paths,
                len(boundaries),
            )
        )
        return results

    def _validate_scope(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        contract_errors = 0
        field_errors = 0
        mutable = _mutable_fields(self.logistics_config)
        if tuple(tables) != self.settings.table_order or tuple(
            source_tables
        ) != self.settings.table_order:
            contract_errors += 1
        for table_name in self.settings.table_order:
            if table_name not in tables or table_name not in source_tables:
                contract_errors += 1
                continue
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].reset_index(drop=True)
            if result.columns.tolist() != source.columns.tolist():
                contract_errors += 1
                continue
            expected_rows = len(source) + (
                self.expected_duplicate_count() if table_name == "shipments" else 0
            )
            if len(result) != expected_rows:
                contract_errors += 1
                continue
            base_rows = result.iloc[: len(source)].reset_index(drop=True)
            for field_name in source.columns:
                if field_name in mutable[table_name]:
                    continue
                if _normalized_values(source[field_name]) != _normalized_values(
                    base_rows[field_name]
                ):
                    field_errors += 1
        return [
            _zero_count_result(
                "logistics.imperfection_scope.table_contract",
                contract_errors,
                "table contract violation(s)",
            ),
            _zero_count_result(
                "logistics.imperfection_scope.unapproved_fields",
                field_errors,
                "unapproved field modification(s)",
            ),
        ]

    def _validate_relational_invariants(
        self,
        tables: dict[str, Any],
    ) -> IntegrityCheckResult:
        results = LogisticsRelationalValidator(self.generator).validate_tables(tables)
        failures = [result.check_name for result in results if not result.passed]
        return _condition_result(
            "logistics.relational_invariants",
            not failures,
            f"all {len(results)} relational checks passed",
            f"failed checks: {failures[:10]}",
        )

    def _fresh_distributed_tables(self) -> dict[str, Any]:
        return LogisticsDistributionApplier(
            DeterministicGenerator(self.settings)
        ).generate_distributed_tables()

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Logistics CSVs are missing. Export the requested profile first "
                "or use generated validation. Missing: "
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
            for field in self.logistics_config["tables"][table_name]["fields"]:
                name = field["name"]
                if field["type"] == "integer":
                    table[name] = table[name].map(
                        lambda value: "" if value == "" else int(value)
                    )
                elif field["type"] == "boolean":
                    table[name] = table[name].map(
                        lambda value: str(value).lower() == "true"
                    )
            tables[table_name] = table
        return tables


def _valid_duplicate_group(
    group: Any,
    business_keys: set[str],
    variation_fields: set[str],
) -> bool:
    if len(group) != 2:
        return False
    ignored = business_keys | variation_fields | {"shipment_id"}
    stable = [field for field in group.columns if field not in ignored]
    first, second = group.iloc[0], group.iloc[1]
    return all(str(first[field]) == str(second[field]) for field in stable) and any(
        str(first[field]) != str(second[field]) for field in variation_fields
    )


def _mutable_fields(config: dict[str, Any]) -> dict[str, set[str]]:
    mutable = {table_name: set() for table_name in config["tables"]}
    targets = config["imperfection_targets"]
    for target_name in (
        "missing_order_warehouses",
        "orphaned_order_warehouses",
        "order_total_outliers",
    ):
        target = targets[target_name]
        mutable[target["table"]].add(target["field"])
    boundary = targets["coordinated_boundary_dates"]
    for qualified in [boundary["primary_target"], *boundary["dependent_targets"]]:
        table_name, field_name = qualified.split(".", 1)
        mutable[table_name].add(field_name)
    return mutable


def _normalized_values(series: Any) -> list[str]:
    return [_normalized_value(value) for value in series.tolist()]


def _normalized_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if _is_blank(value):
        return ""
    return str(value)


def _exact_count_result(
    check_name: str,
    actual: int,
    expected: int,
) -> IntegrityCheckResult:
    if actual == expected:
        return passed(check_name, f"{actual} row(s)")
    return failed(check_name, f"expected {expected}, got {actual}")


def _zero_count_result(
    check_name: str,
    count: int,
    label: str,
) -> IntegrityCheckResult:
    if count == 0:
        return passed(check_name, "zero violations")
    return failed(check_name, f"{count} {label}")


def _condition_result(
    check_name: str,
    condition: bool,
    success: str,
    failure: str,
) -> IntegrityCheckResult:
    return passed(check_name, success) if condition else failed(check_name, failure)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Create the root .venv and "
            "install requirements.txt before validating Logistics imperfections."
        ) from exc
    return pd


__all__ = ["LogisticsImperfectionRateValidator"]
