"""Distribution-specific validation for generated Finance tables."""

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
from generators.finance.config import load_base_config
from generators.finance.config import load_finance_config
from generators.finance.validators.generated_tables import (
    validate_finance_generated_tables,
)


class FinanceDistributedTablesValidator:
    """Validate Finance distributions without duplicating clean-table guards.

    The clean-table validator first proves the complete schema, key, hierarchy,
    FX lookup, accounting, budget-path, and row-count contract. This validator
    then enforces effective distribution bounds, posting-frequency variation,
    configured temporal windows, bounded FX movement, and optional source-table
    preservation for every field outside the declared distribution targets.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError(
                "FinanceDistributedTablesValidator only supports the finance domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.base_config = load_base_config()
        self.finance_config = load_finance_config()
        self.generation_rules = self.finance_config["generation_rules"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate distributed tables and optionally compare their source."""

        validate_finance_generated_tables(self.generator, tables)
        self._validate_amount_distributions(tables)
        self._validate_posting_pair_frequency(tables)
        self._validate_distribution_date_windows(tables)
        self._validate_fx_movement(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_amount_distributions(self, tables: dict[str, Any]) -> None:
        """Require Pareto outputs to remain within effective config bounds."""

        for table_name, column_name, settings_key in (
            ("transactions", "total_amount", "transaction_amount"),
            ("budgets", "budget_amount", "budget_amount"),
        ):
            spec = self.settings.distributions[settings_key]
            scale = int(spec["scale"])
            minimum = Decimal(str(spec["min_amount"]))
            maximum = Decimal(str(spec["max_amount"]))
            for value in tables[table_name][column_name]:
                amount = _parse_fixed_decimal(
                    value,
                    scale,
                    f"{table_name}.{column_name}",
                )
                if amount < minimum or amount > maximum:
                    raise ValueError(
                        f"{table_name}.{column_name} is outside configured "
                        "distribution bounds"
                    )

    def _validate_posting_pair_frequency(self, tables: dict[str, Any]) -> None:
        """Require Poisson frequencies to reconcile and vary by transaction."""

        ledger = tables["ledger_entries"]
        expected_rows = self.generator.row_count("ledger_entries")
        if len(ledger) != expected_rows:
            raise ValueError(
                "Distributed ledger count differs from configured row target"
            )
        line_counts = ledger.groupby("transaction_id").size()
        if int(line_counts.sum()) != expected_rows:
            raise ValueError("Ledger frequencies do not reconcile to row target")
        if (line_counts % 2).any():
            raise ValueError("Distributed ledger contains an odd posting line count")
        pair_counts = line_counts // 2
        if len(pair_counts) > 1 and int(pair_counts.nunique()) == 1:
            raise ValueError("Ledger posting-pair frequencies do not vary")

    def _validate_distribution_date_windows(self, tables: dict[str, Any]) -> None:
        """Check clustered dates and dependent timestamps against config."""

        windows = self.generation_rules["date_windows"]
        entity_start = date.fromisoformat(windows["entity_created_start"])
        reference = self.settings.reference_today
        _assert_timestamp_window(
            tables["accounts"]["created_at"],
            entity_start,
            reference,
            "accounts.created_at",
        )

        boundaries = set(self.base_config["imperfections"]["boundary_dates"])
        regular_fx_dates = set(tables["fx_rates"]["rate_date"]) - boundaries
        if not set(tables["transactions"]["transaction_date"]).issubset(
            regular_fx_dates
        ):
            raise ValueError(
                "transactions.transaction_date is outside the regular FX calendar"
            )

        ledger_lag = self.generation_rules["ledger_entries"]["created_lag_hours"]
        minimum_lag = timedelta(hours=int(ledger_lag["minimum"]))
        maximum_lag = timedelta(hours=int(ledger_lag["maximum"]))
        posted_at = {
            int(row.transaction_id): datetime.fromisoformat(row.posted_at)
            for row in tables["transactions"].itertuples(index=False)
        }
        for row in tables["ledger_entries"].itertuples(index=False):
            lag = datetime.fromisoformat(row.created_at) - posted_at[row.transaction_id]
            if lag < minimum_lag or lag > maximum_lag:
                raise ValueError(
                    "ledger_entries.created_at is outside its configured lag window"
                )

        for row in tables["budgets"].itertuples(index=False):
            created = datetime.fromisoformat(row.created_at)
            upper = datetime.combine(
                date.fromisoformat(row.period_start) - timedelta(days=1),
                time.max,
            )
            if created < datetime.combine(entity_start, time.min) or created > upper:
                raise ValueError(
                    "budgets.created_at is outside its configured distribution window"
                )

    def _validate_fx_movement(self, tables: dict[str, Any]) -> None:
        """Check positive scale-6 FX values against bounded daily movement."""

        fx_rates = tables["fx_rates"]
        rules = self.generation_rules["fx_daily_movement"]
        anchors = self.finance_config["business_mappings"]["fx_anchor_rates"]
        scale = int(rules["scale"])
        mean_reversion = Decimal(rules["mean_reversion_fraction"])
        maximum_bps = Decimal(int(rules["maximum_daily_change_bps"]))
        rounding_tolerance = Decimal(1).scaleb(-scale)

        for currencies, rows in fx_rates.groupby(
            ["from_currency", "to_currency"], sort=False
        ):
            pair = f"{currencies[0]}/{currencies[1]}"
            anchor = Decimal(anchors[pair])
            previous = anchor
            for value in rows["rate"]:
                rate = _parse_fixed_decimal(value, scale, "fx_rates.rate")
                if pair == rules["identity_pair"]:
                    if rate != Decimal(rules["identity_rate"]):
                        raise ValueError("Finance identity FX rate is not exact")
                else:
                    center = previous - ((previous - anchor) * mean_reversion)
                    maximum_shock = anchor * maximum_bps / Decimal(10000)
                    if abs(rate - center) > maximum_shock + rounding_tolerance:
                        raise ValueError(
                            "fx_rates.rate exceeds configured bounded movement"
                        )
                previous = rate

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Reject row, key, relationship, or undeclared field drift."""

        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Finance table order differs from config")
        mutable_fields = _distribution_mutable_fields(self.finance_config)
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

            primary_keys = primary_key_fields(
                self.finance_config["tables"][table_name]
            )
            for key in primary_keys:
                if not source[key].equals(distributed[key]):
                    raise ValueError(
                        f"{table_name}.{key} changed during distribution application"
                    )

            for column_name in source.columns:
                if column_name not in mutable_fields[table_name] and not source[
                    column_name
                ].equals(distributed[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "distribution application"
                    )

        source_posted = set(source_tables["ledger_entries"]["transaction_id"])
        distributed_posted = set(tables["ledger_entries"]["transaction_id"])
        if source_posted != distributed_posted:
            raise ValueError(
                "Posted transaction membership changed during distribution application"
            )


def validate_finance_distributed_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate distributed Finance guard."""

    FinanceDistributedTablesValidator(generator).validate(tables, source_tables)


def _distribution_mutable_fields(config: dict[str, Any]) -> dict[str, set[str]]:
    """Derive mutable fields from direct targets and dependent rebuilds."""

    mutable = {table_name: set() for table_name in config["tables"]}
    for target in config["distribution_targets"].values():
        table_name = target.get("table")
        field_name = target.get("field")
        if table_name in mutable and field_name is not None:
            mutable[table_name].add(field_name)
        for dotted_target in target.get("targets", []):
            dotted_table, dotted_field = dotted_target.split(".", 1)
            mutable[dotted_table].add(dotted_field)
        for rebuilt_table in (target.get("dependent_rebuild"),):
            if rebuilt_table not in mutable:
                continue
            primary_keys = set(primary_key_fields(config["tables"][rebuilt_table]))
            mutable[rebuilt_table].update(
                field["name"]
                for field in config["tables"][rebuilt_table]["fields"]
                if field["name"] not in primary_keys
            )
        if target.get("distribution") == "poisson" and table_name in mutable:
            primary_keys = set(primary_key_fields(config["tables"][table_name]))
            mutable[table_name].update(
                field["name"]
                for field in config["tables"][table_name]["fields"]
                if field["name"] not in primary_keys
            )
    return mutable


def _parse_fixed_decimal(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains a non-decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} does not use scale {scale}")
    return parsed


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


__all__ = [
    "FinanceDistributedTablesValidator",
    "validate_finance_distributed_tables",
]
