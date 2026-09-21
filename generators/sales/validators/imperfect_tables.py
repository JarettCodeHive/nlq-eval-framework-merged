"""Immediate validation for controlled Sales imperfection results."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import primary_key_fields
from generators.sales.config import load_sales_config
from generators.sales.validators.distributed_tables import (
    validate_sales_distributed_tables,
)


_IMPERFECTION_MUTABLE_FIELDS = {
    "leads": set(),
    "deals": {"deal_amount"},
    "products": {"list_price", "created_at"},
    "quotations": set(),
    "targets": set(),
}


class SalesImperfectTablesValidator:
    """Permit only configured Sales defects and reject collateral damage."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError(
                "SalesImperfectTablesValidator only supports the sales domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.sales_config = load_sales_config()

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate structure, defect counts, relationships, and source drift."""

        if source_tables is not None:
            validate_sales_distributed_tables(self.generator, source_tables)
        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_foreign_keys_and_bridge(tables)
        self._validate_missing_product_prices(tables)
        self._validate_duplicate_quotations(tables)
        self._validate_deal_outliers(tables)
        self._validate_boundary_timestamps(tables)
        self._validate_currency(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Imperfect Sales table order differs from config")
        expected_duplicates = self._expected_duplicate_count()
        for table_name in self.settings.table_order:
            table = tables[table_name]
            expected_columns = [
                field["name"]
                for field in self.sales_config["tables"][table_name]["fields"]
            ]
            if table.columns.tolist() != expected_columns:
                raise ValueError(
                    f"{table_name} columns changed after Sales imperfections"
                )
            expected_rows = self.generator.row_count(table_name)
            if table_name == "quotations":
                expected_rows += expected_duplicates
            if len(table) != expected_rows:
                raise ValueError(
                    f"{table_name} imperfect row count differs from expected: "
                    f"expected {expected_rows}, got {len(table)}"
                )
            if len(table) > self.settings.max_rows_per_table:
                raise ValueError(f"{table_name} exceeds the configured row cap")

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.sales_config["tables"][table_name]
            table = tables[table_name]
            primary_keys = primary_key_fields(table_config)
            if any(_blank_count(table[field]) for field in primary_keys):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(primary_keys).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                name = field["name"]
                if not field["nullable"] and _blank_count(table[name]):
                    raise ValueError(
                        f"Required field contains blanks: {table_name}.{name}"
                    )
                if field.get("key") == "unique" and table[name].duplicated().any():
                    raise ValueError(f"{table_name}.{name} contains duplicate values")

    def _validate_foreign_keys_and_bridge(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.sales_config["tables"].items():
            for field in table_config["fields"]:
                reference = field.get("references")
                if reference is None:
                    continue
                parent_values = set(
                    tables[reference["table"]][reference["field"]].tolist()
                )
                child_values = {
                    value
                    for value in tables[table_name][field["name"]].tolist()
                    if not _is_blank(value)
                }
                if child_values - parent_values:
                    raise ValueError(
                        f"{table_name}.{field['name']} contains orphan values"
                    )

        quotations = tables["quotations"]
        if quotations.groupby("deal_id")["product_id"].nunique().max() < 2:
            raise ValueError("Imperfect Sales data lacks a multi-product deal")
        if quotations.groupby("product_id")["deal_id"].nunique().max() < 2:
            raise ValueError("Imperfect Sales data lacks a multi-deal product")
        if set(quotations["deal_id"]) != set(tables["deals"]["deal_id"]):
            raise ValueError("Imperfect quotations do not cover every deal")
        if set(quotations["product_id"]) != set(tables["products"]["product_id"]):
            raise ValueError("Imperfect quotations do not cover every product")

    def _validate_missing_product_prices(self, tables: dict[str, Any]) -> None:
        expected = count_from_pct(
            self.generator.row_count("products"),
            float(self.config["null_pct"]),
        )
        actual = _blank_count(tables["products"]["list_price"])
        if actual != expected:
            raise ValueError(
                "products.list_price NULL count differs from configured rate: "
                f"expected {expected}, got {actual}"
            )
        if _blank_count(tables["quotations"]["unit_price"]):
            raise ValueError("Quotation unit prices changed with missing list prices")

    def _validate_duplicate_quotations(self, tables: dict[str, Any]) -> None:
        quotations = tables["quotations"]
        base_count = self.generator.row_count("quotations")
        expected = self._expected_duplicate_count()
        base_rows = quotations.iloc[:base_count]
        duplicate_rows = quotations.iloc[base_count:]
        if len(duplicate_rows) != expected:
            raise ValueError("Near-duplicate quotation count differs from config")

        business_keys = self.sales_config["imperfection_targets"][
            "near_duplicate_quotation_lines"
        ]["business_key_fields"]
        variation_fields = set(
            self.sales_config["imperfection_targets"]["near_duplicate_quotation_lines"][
                "variation_fields"
            ]
        )
        if base_rows.duplicated(business_keys).any():
            raise ValueError("Base quotation business keys are not unique")
        source_by_key = base_rows.set_index(business_keys, drop=False)
        source_max_id = int(base_rows["quotation_id"].max())
        expected_ids = list(range(source_max_id + 1, source_max_id + expected + 1))
        if duplicate_rows["quotation_id"].tolist() != expected_ids:
            raise ValueError(
                "Near-duplicate quotation IDs are not fresh and sequential"
            )

        unchanged_fields = (
            set(quotations.columns)
            - variation_fields
            - {
                "quotation_id",
            }
        )
        for row in duplicate_rows.itertuples(index=False):
            key = tuple(getattr(row, field) for field in business_keys)
            lookup_key: Any = key[0] if len(key) == 1 else key
            if lookup_key not in source_by_key.index:
                raise ValueError(
                    "Near-duplicate quotation has no matching source business key"
                )
            source = source_by_key.loc[lookup_key]
            if any(getattr(row, field) != source[field] for field in unchanged_fields):
                raise ValueError(
                    "Near-duplicate quotation changed a non-variation field"
                )
            if not any(
                getattr(row, field) != source[field] for field in variation_fields
            ):
                raise ValueError("Near-duplicate quotation is byte-identical")

        actual_duplicate_pairs = int(
            quotations.duplicated(["deal_id", "product_id"]).sum()
        )
        if actual_duplicate_pairs != expected:
            raise ValueError(
                "Duplicate deal/product pair count differs from configured rate"
            )

    def _validate_deal_outliers(self, tables: dict[str, Any]) -> None:
        target = self.sales_config["imperfection_targets"]["deal_amount_outliers"]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        scale = int(target["scale"])
        expected = count_from_pct(
            self.generator.row_count("deals"),
            float(self.config["outlier_pct"]),
        )
        outliers: list[Decimal] = []
        for value in tables["deals"]["deal_amount"]:
            try:
                amount = Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError(
                    "deals.deal_amount contains a non-decimal value"
                ) from exc
            if amount >= minimum:
                outliers.append(amount)
        if len(outliers) != expected:
            raise ValueError(
                "deals.deal_amount outlier count differs from configured rate: "
                f"expected {expected}, got {len(outliers)}"
            )
        if any(value > maximum for value in outliers):
            raise ValueError("A deal-amount outlier exceeds its configured maximum")
        if any(value.as_tuple().exponent != -scale for value in outliers):
            raise ValueError(f"Deal-amount outliers do not use scale {scale}")

    def _validate_boundary_timestamps(self, tables: dict[str, Any]) -> None:
        target = self.sales_config["imperfection_targets"][
            "product_created_boundary_timestamps"
        ]
        expected = [
            f"{value}T{target['timestamp_time']}"
            for value in self.config["boundary_dates"]
        ]
        actual = tables["products"]["created_at"].iloc[: len(expected)].tolist()
        if actual != expected:
            raise ValueError("products.created_at boundary values are missing or moved")

    def _validate_currency(self, tables: dict[str, Any]) -> None:
        currency = self.sales_config["business_mappings"]["currency_code"]
        for table_name in ("deals", "products", "targets"):
            if set(tables[table_name]["currency_code"]) != {currency}:
                raise ValueError(f"{table_name} contains non-USD currency values")

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        for table_name in self.settings.table_order:
            source = source_tables[table_name]
            result = tables[table_name].iloc[: len(source)].reset_index(drop=True)
            source = source.reset_index(drop=True)
            mutable = _IMPERFECTION_MUTABLE_FIELDS[table_name]
            for column_name in source.columns:
                if column_name not in mutable and not source[column_name].equals(
                    result[column_name]
                ):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "imperfection injection"
                    )

    def _expected_duplicate_count(self) -> int:
        return count_from_pct(
            self.generator.row_count("quotations"),
            float(self.config["duplicate_pct"]),
        )


def validate_sales_imperfect_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate Sales imperfection-result guard."""

    SalesImperfectTablesValidator(generator).validate(tables, source_tables)


def _blank_count(series: Any) -> int:
    return int(series.isna().sum() + series.astype(str).eq("").sum())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "SalesImperfectTablesValidator",
    "validate_sales_imperfect_tables",
]
