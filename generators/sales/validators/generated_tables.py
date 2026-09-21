"""Immediate structural and relational guards for clean Sales tables."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.sales.config import load_sales_config


class SalesGeneratedTablesValidator:
    """Reject invalid clean Sales tables before a generation stage returns."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError(
                "SalesGeneratedTablesValidator only supports the sales domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()

    def validate(self, tables: dict[str, Any]) -> None:
        """Validate clean table shape, relationships, and business invariants."""

        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_foreign_keys(tables)
        self._validate_leads_and_deals(tables)
        self._validate_products_and_quotations(tables)
        self._validate_representatives_and_targets(tables)
        self._validate_currency(tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Generated Sales table order differs from config")
        for table_name in self.settings.table_order:
            expected_columns = [
                field["name"]
                for field in self.sales_config["tables"][table_name]["fields"]
            ]
            actual_columns = tables[table_name].columns.tolist()
            if actual_columns != expected_columns:
                raise ValueError(f"{table_name} generated columns differ from contract")
            expected_rows = self.generator.row_count(table_name)
            if len(tables[table_name]) != expected_rows:
                raise ValueError(
                    f"{table_name} generated row count differs from config: "
                    f"expected {expected_rows}, got {len(tables[table_name])}"
                )

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.sales_config["tables"][table_name]
            table = tables[table_name]
            key_fields = primary_key_fields(table_config)
            if any(_has_blanks(table[field]) for field in key_fields):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(key_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _has_blanks(table[field["name"]]):
                    raise ValueError(
                        f"{table_name}.{field['name']} contains blank required values"
                    )

    def _validate_foreign_keys(self, tables: dict[str, Any]) -> None:
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
                missing = child_values - parent_values
                if missing:
                    raise ValueError(
                        f"{table_name}.{field['name']} contains orphan values"
                    )

    def _validate_leads_and_deals(self, tables: dict[str, Any]) -> None:
        leads = tables["leads"]
        deals = tables["deals"]
        lead_lookup = leads.set_index("lead_id")
        converted = self.sales_config["business_mappings"]["lead_conversion"][
            "deal_bearing_status"
        ]
        deal_rules = self.sales_config["generation_rules"]["deals"]
        closed_stages = set(deal_rules["closed_stages"])
        open_stages = set(deal_rules["open_stages"])
        for row in deals.itertuples(index=False):
            lead = lead_lookup.loc[row.lead_id]
            if lead["lead_status"] != converted:
                raise ValueError("A deal references a lead that is not Converted")
            if lead["rep_name"] != row.rep_name:
                raise ValueError("Deal representative differs from source lead")
            created = datetime.fromisoformat(row.created_at)
            if created < datetime.fromisoformat(lead["created_at"]):
                raise ValueError("Deal creation precedes source-lead creation")
            if date.fromisoformat(row.expected_close_date) < created.date():
                raise ValueError("Deal expected close date precedes creation")
            if row.stage in closed_stages:
                if not row.close_date:
                    raise ValueError("Closed deal lacks close_date")
                if date.fromisoformat(row.close_date) < created.date():
                    raise ValueError("Deal close date precedes creation")
            elif row.stage in open_stages and row.close_date:
                raise ValueError("Open deal unexpectedly has close_date")
        if not (set(leads["lead_id"]) - set(deals["lead_id"])):
            raise ValueError("Sales base data lacks leads without deals")

    def _validate_products_and_quotations(self, tables: dict[str, Any]) -> None:
        products = tables["products"]
        deals = tables["deals"]
        quotations = tables["quotations"]
        if products["sku"].duplicated().any():
            raise ValueError("Generated Sales product SKUs are not unique")
        if _has_blanks(products["list_price"]):
            raise ValueError("Base Sales products contain missing list prices")
        if quotations.duplicated(["deal_id", "product_id"]).any():
            raise ValueError(
                "Base Sales quotations contain duplicate deal/product pairs"
            )
        if quotations.groupby("deal_id")["product_id"].nunique().max() < 2:
            raise ValueError("Sales quotations lack a multi-product deal")
        if quotations.groupby("product_id")["deal_id"].nunique().max() < 2:
            raise ValueError("Sales quotations lack a product used by multiple deals")
        if set(quotations["deal_id"]) != set(deals["deal_id"]):
            raise ValueError("Sales quotations do not cover every deal")
        if set(quotations["product_id"]) != set(products["product_id"]):
            raise ValueError("Sales quotations do not cover every product")

        deal_windows = {
            int(row.deal_id): (
                datetime.fromisoformat(row.created_at),
                (
                    datetime.combine(date.fromisoformat(row.close_date), time.max)
                    if row.close_date
                    else datetime.combine(self.settings.reference_today, time.max)
                ),
            )
            for row in deals.itertuples(index=False)
        }
        for row in quotations.itertuples(index=False):
            created = datetime.fromisoformat(row.created_at)
            quoted = datetime.fromisoformat(row.quoted_at)
            deal_created, deal_end = deal_windows[row.deal_id]
            if not deal_created <= created <= quoted <= deal_end:
                raise ValueError("Quotation timestamps are outside the deal window")
            if int(row.quantity) <= 0 or float(row.unit_price) <= 0:
                raise ValueError("Quotation quantity and unit price must be positive")

    def _validate_representatives_and_targets(self, tables: dict[str, Any]) -> None:
        leads = tables["leads"]
        deals = tables["deals"]
        targets = tables["targets"]
        lead_territories = _single_territory_by_rep(leads, "leads")
        target_territories = _single_territory_by_rep(targets, "targets")
        if set(lead_territories) != set(target_territories):
            raise ValueError(
                "Representative coverage differs between leads and targets"
            )
        if lead_territories != target_territories:
            raise ValueError("Representative territories differ across Sales tables")
        if not set(deals["rep_name"]).issubset(set(target_territories)):
            raise ValueError("A deal representative has no configured target")

        rules = self.sales_config["generation_rules"]
        expected_reps = int(
            rules["representatives"]["pool_sizes"][self.settings.profile]
        )
        expected_periods = int(
            rules["targets"]["periods_per_representative"][self.settings.profile]
        )
        if targets["rep_name"].nunique() != expected_reps:
            raise ValueError("Sales target representative count differs from config")
        for rep_name, periods in targets.groupby("rep_name"):
            ordered = periods.sort_values("period_start")
            if len(ordered) != expected_periods:
                raise ValueError(
                    f"Target period count differs for representative {rep_name}"
                )
            previous_end: date | None = None
            for row in ordered.itertuples(index=False):
                start = date.fromisoformat(row.period_start)
                end = date.fromisoformat(row.period_end)
                if start >= end:
                    raise ValueError("Sales target period is empty or reversed")
                if previous_end is not None and start < previous_end:
                    raise ValueError("Sales target periods overlap")
                if datetime.fromisoformat(row.created_at).date() >= start:
                    raise ValueError("Sales target creation is not before its period")
                previous_end = end

        won_stage = self.sales_config["business_mappings"]["quota_attainment"][
            "attained_stage"
        ]
        won_deals = deals[deals["stage"] == won_stage]
        matched_targets: set[int] = set()
        for target in targets.itertuples(index=False):
            matching = won_deals[
                (won_deals["rep_name"] == target.rep_name)
                & (won_deals["close_date"] >= target.period_start)
                & (won_deals["close_date"] < target.period_end)
            ]
            if not matching.empty:
                matched_targets.add(int(target.target_id))
        if not matched_targets:
            raise ValueError("Sales base data lacks a non-empty quota-attainment path")
        if len(matched_targets) == len(targets):
            raise ValueError("Sales base data lacks a zero-attainment target")

    def _validate_currency(self, tables: dict[str, Any]) -> None:
        currency = self.sales_config["business_mappings"]["currency_code"]
        for table_name in ("deals", "products", "targets"):
            if set(tables[table_name]["currency_code"]) != {currency}:
                raise ValueError(f"{table_name} contains non-USD currency values")


def validate_sales_generated_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
) -> None:
    """Run all immediate clean Sales table guards or raise on first failure."""

    SalesGeneratedTablesValidator(generator).validate(tables)


def _single_territory_by_rep(table: Any, label: str) -> dict[str, str]:
    pairs = table[["rep_name", "territory"]].drop_duplicates()
    counts = pairs.groupby("rep_name").size()
    if not counts.empty and int(counts.max()) > 1:
        raise ValueError(f"A representative has multiple territories in {label}")
    return dict(pairs.itertuples(index=False, name=None))


def _has_blanks(series: Any) -> bool:
    return bool(series.isna().any() or series.eq("").any())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = ["SalesGeneratedTablesValidator", "validate_sales_generated_tables"]
