"""Distribution-specific validation for generated Sales tables."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.sales.config import load_sales_config
from generators.sales.validators.generated_tables import (
    validate_sales_generated_tables,
)


_DISTRIBUTION_MUTABLE_FIELDS = {
    "leads": {"created_at"},
    "deals": {
        "deal_amount",
        "close_date",
        "expected_close_date",
        "created_at",
    },
    "products": {"created_at"},
    "quotations": {
        "deal_id",
        "product_id",
        "quote_number",
        "unit_price",
        "quoted_at",
        "created_at",
    },
    "targets": {"quota_amount"},
}


class SalesDistributedTablesValidator:
    """Validate distribution outputs without duplicating relational guards.

    Clean-table validation first proves the complete schema, row-count, key,
    relationship, ownership, chronology, quota-path, and currency contract.
    This validator then checks effective distribution bounds, decimal scales,
    quotation-frequency behavior, configured temporal windows, and optional
    source-table preservation.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError(
                "SalesDistributedTablesValidator only supports the sales domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()
        self.generation_rules = self.sales_config["generation_rules"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate distributed tables and optionally compare their source."""

        validate_sales_generated_tables(self.generator, tables)
        self._validate_amount_distributions(tables)
        self._validate_quotation_frequency(tables)
        self._validate_distribution_date_windows(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_amount_distributions(self, tables: dict[str, Any]) -> None:
        for table_name, column_name, settings_key in (
            ("deals", "deal_amount", "deal_amount"),
            ("targets", "quota_amount", "quota_amount"),
        ):
            spec = self.settings.distributions[settings_key]
            scale = int(spec["scale"])
            minimum = Decimal(str(spec["min_amount"]))
            maximum = Decimal(str(spec["max_amount"]))
            for value in tables[table_name][column_name]:
                try:
                    amount = Decimal(str(value))
                except InvalidOperation as exc:
                    raise ValueError(
                        f"{table_name}.{column_name} contains a non-decimal value"
                    ) from exc
                if amount < minimum or amount > maximum:
                    raise ValueError(
                        f"{table_name}.{column_name} is outside configured "
                        "distribution bounds"
                    )
                if amount.as_tuple().exponent != -scale:
                    raise ValueError(
                        f"{table_name}.{column_name} does not use scale {scale}"
                    )

    def _validate_quotation_frequency(self, tables: dict[str, Any]) -> None:
        quotations = tables["quotations"]
        expected_rows = self.generator.row_count("quotations")
        if len(quotations) != expected_rows:
            raise ValueError(
                "Distributed quotation count differs from configured row target"
            )
        frequencies = quotations.groupby("deal_id").size()
        if len(frequencies) != len(tables["deals"]):
            raise ValueError("Distributed quotations do not cover every deal")
        if int(frequencies.sum()) != expected_rows:
            raise ValueError("Quotation frequencies do not reconcile to row target")
        if len(frequencies) > 1 and int(frequencies.nunique()) == 1:
            raise ValueError("Quotation frequencies do not vary between deals")

    def _validate_distribution_date_windows(self, tables: dict[str, Any]) -> None:
        windows = self.generation_rules["date_windows"]
        reference = self.settings.reference_today
        entity_start = date.fromisoformat(windows["entity_created_start"])
        activity_start = date.fromisoformat(windows["deal_activity_start"])
        quotation_start = date.fromisoformat(windows["quotation_activity_start"])
        expected_maximum = reference + timedelta(
            days=int(
                self.generation_rules["deals"]["expected_close_offset_days"]["maximum"]
            )
        )

        for table_name in ("leads", "products"):
            _assert_timestamp_window(
                tables[table_name]["created_at"],
                entity_start,
                reference,
                f"{table_name}.created_at",
            )
        _assert_timestamp_window(
            tables["deals"]["created_at"],
            activity_start,
            reference,
            "deals.created_at",
        )
        _assert_date_window(
            tables["deals"]["close_date"],
            activity_start,
            reference,
            "deals.close_date",
            allow_blank=True,
        )
        _assert_date_window(
            tables["deals"]["expected_close_date"],
            activity_start,
            expected_maximum,
            "deals.expected_close_date",
        )
        for column_name in ("created_at", "quoted_at"):
            _assert_timestamp_window(
                tables["quotations"][column_name],
                quotation_start,
                reference,
                f"quotations.{column_name}",
            )

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Sales table order differs from config")
        for table_name in self.settings.table_order:
            source = source_tables[table_name]
            distributed = tables[table_name]
            if source.columns.tolist() != distributed.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during distribution application"
                )
            if len(source) != len(distributed):
                raise ValueError(
                    f"{table_name} row count changed during distribution application"
                )

            primary_keys = primary_key_fields(self.sales_config["tables"][table_name])
            for key in primary_keys:
                if not source[key].equals(distributed[key]):
                    raise ValueError(
                        f"{table_name}.{key} changed during distribution application"
                    )

            mutable = _DISTRIBUTION_MUTABLE_FIELDS[table_name]
            for column_name in source.columns:
                if column_name not in mutable and not source[column_name].equals(
                    distributed[column_name]
                ):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "distribution application"
                    )


def validate_sales_distributed_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate distributed Sales guard."""

    SalesDistributedTablesValidator(generator).validate(tables, source_tables)


def _assert_timestamp_window(
    values: Any,
    start: date,
    end: date,
    label: str,
) -> None:
    lower = datetime.combine(start, time.min)
    upper = datetime.combine(end, time.max)
    for value in values:
        parsed = datetime.fromisoformat(str(value))
        if parsed < lower or parsed > upper:
            raise ValueError(f"{label} is outside its configured distribution window")


def _assert_date_window(
    values: Any,
    start: date,
    end: date,
    label: str,
    allow_blank: bool = False,
) -> None:
    for value in values:
        if allow_blank and value == "":
            continue
        parsed = date.fromisoformat(str(value))
        if parsed < start or parsed > end:
            raise ValueError(f"{label} is outside its configured distribution window")


__all__ = [
    "SalesDistributedTablesValidator",
    "validate_sales_distributed_tables",
]
