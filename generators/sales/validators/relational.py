"""Full relational and business-rule validation for Sales datasets."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import non_empty_values
from generators.core.integrity import passed
from generators.core.schema_contract import primary_key_fields
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config


class SalesRelationalValidator:
    """Report complete relational validity for final Sales tables."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesRelationalValidator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()

    @classmethod
    def for_profile(cls, profile: str) -> "SalesRelationalValidator":
        """Create a relational validator from validated Sales config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final imperfect Sales tables and validate them."""

        tables = SalesImperfectionInjector(self.generator).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return all safely applicable relational checks for Sales tables."""

        results = self._validate_table_contract(tables)
        if any(not result.passed for result in results):
            return results

        column_results = self._validate_columns(tables)
        results.extend(column_results)
        if any(not result.passed for result in column_results):
            return results

        results.extend(self._validate_row_counts(tables))
        results.extend(self._validate_keys(tables))
        results.extend(self._validate_required_fields(tables))
        results.extend(self._validate_foreign_keys(tables))
        results.extend(self._validate_leads_and_deals(tables))
        results.extend(self._validate_products_and_quotations(tables))
        results.extend(self._validate_deal_temporal_rules(tables))
        results.extend(self._validate_quotation_temporal_rules(tables))
        results.extend(self._validate_targets(tables))
        results.extend(self._validate_representatives(tables))
        results.extend(self._validate_quota_paths(tables))
        results.extend(self._validate_currency(tables))
        return results

    def validate_or_raise(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate tables and raise one combined error for all failures."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def _validate_table_contract(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.settings.table_order
        actual = tuple(tables)
        results = [
            _result(
                "schema.table_order",
                actual == expected,
                "all tables present in dependency order",
                f"expected {list(expected)}, got {list(actual)}",
            )
        ]
        for table_name in expected:
            results.append(
                _result(
                    f"{table_name}.present",
                    table_name in tables,
                    "table present",
                    "table missing",
                )
            )
        extra = sorted(set(tables) - set(expected))
        results.append(
            _result(
                "schema.unexpected_tables",
                not extra,
                "no unexpected tables",
                f"unexpected tables: {extra}",
            )
        )
        return results

    def _validate_columns(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            expected = [
                field["name"]
                for field in self.sales_config["tables"][table_name]["fields"]
            ]
            actual = tables[table_name].columns.tolist()
            results.append(
                _result(
                    f"{table_name}.columns",
                    actual == expected,
                    "columns match config order",
                    f"expected {expected}, got {actual}",
                )
            )
        return results

    def _validate_row_counts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        base_quotation_count = expected_counts["quotations"]
        expected_counts["quotations"] += count_from_pct(
            base_quotation_count,
            float(self.settings.imperfections["duplicate_pct"]),
        )
        results = [
            _result(
                f"{table_name}.row_count",
                len(tables[table_name]) == expected,
                f"final row count {expected}",
                f"expected {expected}, got {len(tables[table_name])}",
            )
            for table_name, expected in expected_counts.items()
        ]
        results.extend(
            _result(
                f"{table_name}.row_cap",
                len(tables[table_name]) <= self.settings.max_rows_per_table,
                f"within row cap {self.settings.max_rows_per_table}",
                f"{len(tables[table_name])} rows exceed cap",
            )
            for table_name in self.settings.table_order
        )
        return results

    def _validate_keys(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            config = self.sales_config["tables"][table_name]
            keys = primary_key_fields(config)
            blank_columns = [
                field for field in keys if empty_count(tables[table_name][field])
            ]
            duplicates = int(tables[table_name].duplicated(keys).sum())
            results.append(
                _result(
                    f"{table_name}.primary_key",
                    not blank_columns and duplicates == 0,
                    "primary key populated and unique",
                    f"blank columns={blank_columns}, duplicate rows={duplicates}",
                )
            )
            for field in config["fields"]:
                if field.get("key") != "unique":
                    continue
                name = field["name"]
                duplicate_count = int(tables[table_name][name].duplicated().sum())
                results.append(
                    _result(
                        f"{table_name}.{name}.unique",
                        duplicate_count == 0,
                        "unique values",
                        f"{duplicate_count} duplicate value(s)",
                    )
                )
        # SKU is a documented business-unique identifier even without a SQL key.
        sku_duplicates = int(tables["products"]["sku"].duplicated().sum())
        results.append(
            _result(
                "products.sku.unique",
                sku_duplicates == 0,
                "product SKU values unique",
                f"{sku_duplicates} duplicate SKU value(s)",
            )
        )
        return results

    def _validate_required_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.sales_config["tables"][table_name]["fields"]:
                if field["nullable"]:
                    continue
                name = field["name"]
                blanks = empty_count(tables[table_name][name])
                results.append(
                    _result(
                        f"{table_name}.{name}.not_null",
                        blanks == 0,
                        "no blank values",
                        f"{blanks} blank value(s)",
                    )
                )
        return results

    def _validate_foreign_keys(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for relationship in self.sales_config["relationships"]:
            if relationship.get("relationship_type") != "foreign_key":
                continue
            parent_table = relationship["parent_table"]
            parent_field = relationship["parent_field"]
            child_table = relationship["child_table"]
            child_field = relationship["child_field"]
            allowed = non_empty_values(tables[parent_table][parent_field])
            actual = non_empty_values(tables[child_table][child_field])
            invalid = sorted(actual - allowed)
            results.append(
                _result(
                    f"{child_table}.{child_field}.fk",
                    not invalid,
                    "all references valid",
                    f"invalid references: {invalid[:5]}",
                )
            )
        return results

    def _validate_leads_and_deals(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        leads = tables["leads"]
        deals = tables["deals"]
        lead_lookup = leads.set_index("lead_id")
        converted = self.sales_config["business_mappings"]["lead_conversion"][
            "deal_bearing_status"
        ]
        invalid_status = 0
        invalid_owner = 0
        for row in deals.itertuples(index=False):
            if row.lead_id not in lead_lookup.index:
                continue
            lead = lead_lookup.loc[row.lead_id]
            invalid_status += lead["lead_status"] != converted
            invalid_owner += lead["rep_name"] != row.rep_name
        unmatched_leads = len(set(leads["lead_id"]) - set(deals["lead_id"]))
        return [
            _count_result("deals.converted_lead", invalid_status),
            _count_result("deals.lead_owner_consistency", invalid_owner),
            _result(
                "leads.without_deals",
                unmatched_leads > 0,
                f"{unmatched_leads} lead(s) have no deal",
                "no unmatched leads remain for LEFT JOIN behavior",
            ),
        ]

    def _validate_products_and_quotations(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        products = tables["products"]
        quotations = tables["quotations"]
        product_prices = products["list_price"].astype(str)
        invalid_prices = sum(
            value != "" and Decimal(value) <= 0 for value in product_prices
        )
        rules = self.sales_config["generation_rules"]["quotations"]
        quantity_minimum = int(rules["quantity"]["minimum"])
        quantity_maximum = int(rules["quantity"]["maximum"])
        discount_minimum = Decimal(str(rules["discount_pct"]["minimum"]))
        discount_maximum = Decimal(str(rules["discount_pct"]["maximum"]))
        invalid_quantities = int(
            (
                ~quotations["quantity"]
                .astype(int)
                .between(quantity_minimum, quantity_maximum)
            ).sum()
        )
        invalid_unit_prices = sum(
            Decimal(str(value)) <= 0 for value in quotations["unit_price"]
        )
        invalid_discounts = sum(
            not discount_minimum <= Decimal(str(value)) <= discount_maximum
            for value in quotations["discount_pct"]
        )
        invalid_statuses = set(quotations["quote_status"]) - set(
            self.sales_config["domain_values"]["quote_statuses"]
        )
        deals_per_product = quotations.groupby("product_id")["deal_id"].nunique()
        products_per_deal = quotations.groupby("deal_id")["product_id"].nunique()
        duplicate_pairs = int(quotations.duplicated(["deal_id", "product_id"]).sum())
        expected_duplicates = count_from_pct(
            self.generator.row_count("quotations"),
            float(self.settings.imperfections["duplicate_pct"]),
        )
        return [
            _count_result("products.list_price.positive", invalid_prices),
            _count_result("quotations.quantity.range", invalid_quantities),
            _count_result("quotations.unit_price.positive", invalid_unit_prices),
            _count_result("quotations.discount_pct.range", invalid_discounts),
            _result(
                "quotations.quote_status.domain",
                not invalid_statuses,
                "all quote statuses are configured",
                f"invalid statuses: {sorted(invalid_statuses)}",
            ),
            _result(
                "quotations.deal_many_products",
                not products_per_deal.empty and int(products_per_deal.max()) >= 2,
                "at least one deal references multiple products",
                "no deal references multiple products",
            ),
            _result(
                "quotations.product_many_deals",
                not deals_per_product.empty and int(deals_per_product.max()) >= 2,
                "at least one product appears on multiple deals",
                "no product appears on multiple deals",
            ),
            _result(
                "quotations.controlled_duplicate_pairs",
                duplicate_pairs == expected_duplicates,
                f"controlled duplicate pair count {expected_duplicates}",
                f"expected {expected_duplicates}, got {duplicate_pairs}",
            ),
        ]

    def _validate_deal_temporal_rules(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        leads = tables["leads"].set_index("lead_id")
        rules = self.sales_config["generation_rules"]["deals"]
        closed_stages = set(rules["closed_stages"])
        open_stages = set(rules["open_stages"])
        invalid_source_order = 0
        invalid_expected_order = 0
        invalid_close_order = 0
        invalid_stage_null = 0
        for row in tables["deals"].itertuples(index=False):
            created = datetime.fromisoformat(str(row.created_at))
            if row.lead_id in leads.index:
                invalid_source_order += created < datetime.fromisoformat(
                    str(leads.loc[row.lead_id, "created_at"])
                )
            invalid_expected_order += (
                row.expected_close_date == ""
                or date.fromisoformat(str(row.expected_close_date)) < created.date()
            )
            if row.stage in closed_stages:
                if row.close_date == "":
                    invalid_stage_null += 1
                else:
                    invalid_close_order += (
                        date.fromisoformat(str(row.close_date)) < created.date()
                    )
            elif row.stage in open_stages:
                invalid_stage_null += row.close_date != ""
            else:
                invalid_stage_null += 1
        return [
            _temporal_result("deals.source_lead_temporal", invalid_source_order),
            _temporal_result("deals.expected_close_temporal", invalid_expected_order),
            _temporal_result("deals.actual_close_temporal", invalid_close_order),
            _count_result("deals.stage_close_date_semantics", invalid_stage_null),
        ]

    def _validate_quotation_temporal_rules(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        reference_end = datetime.combine(self.settings.reference_today, time.max)
        deal_windows = {
            int(row.deal_id): (
                datetime.fromisoformat(str(row.created_at)),
                (
                    datetime.combine(date.fromisoformat(str(row.close_date)), time.max)
                    if row.close_date != ""
                    else reference_end
                ),
            )
            for row in tables["deals"].itertuples(index=False)
        }
        violations = 0
        for row in tables["quotations"].itertuples(index=False):
            if row.deal_id not in deal_windows:
                continue
            created = datetime.fromisoformat(str(row.created_at))
            quoted = datetime.fromisoformat(str(row.quoted_at))
            deal_start, deal_end = deal_windows[row.deal_id]
            violations += not deal_start <= created <= quoted <= deal_end
        return [_temporal_result("quotations.temporal", violations)]

    def _validate_targets(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        invalid_periods = 0
        invalid_creation = 0
        overlap_count = 0
        for _, rows in tables["targets"].groupby("rep_name"):
            previous_end: date | None = None
            for row in rows.sort_values("period_start").itertuples(index=False):
                start = date.fromisoformat(str(row.period_start))
                end = date.fromisoformat(str(row.period_end))
                invalid_periods += start >= end
                invalid_creation += (
                    datetime.fromisoformat(str(row.created_at)).date() >= start
                )
                if previous_end is not None:
                    overlap_count += start < previous_end
                previous_end = end
        return [
            _count_result("targets.period_validity", invalid_periods),
            _temporal_result("targets.creation_temporal", invalid_creation),
            _count_result("targets.period_overlap", overlap_count),
        ]

    def _validate_representatives(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        lead_pairs = tables["leads"][["rep_name", "territory"]].drop_duplicates()
        target_pairs = tables["targets"][["rep_name", "territory"]].drop_duplicates()
        lead_counts = lead_pairs.groupby("rep_name").size()
        target_counts = target_pairs.groupby("rep_name").size()
        lead_territories_unique = lead_counts.empty or int(lead_counts.max()) == 1
        target_territories_unique = target_counts.empty or int(target_counts.max()) == 1
        lead_map = dict(lead_pairs.itertuples(index=False, name=None))
        target_map = dict(target_pairs.itertuples(index=False, name=None))
        deal_reps = set(tables["deals"]["rep_name"])
        return [
            _result(
                "leads.representative_territory",
                lead_territories_unique,
                "one territory per lead representative",
                "a lead representative has multiple territories",
            ),
            _result(
                "targets.representative_territory",
                target_territories_unique,
                "one territory per target representative",
                "a target representative has multiple territories",
            ),
            _result(
                "sales.representative_alignment",
                lead_territories_unique
                and target_territories_unique
                and lead_map == target_map
                and deal_reps.issubset(set(target_map)),
                "representatives and territories align",
                "representative coverage or territories differ",
            ),
        ]

    def _validate_quota_paths(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        won_stage = self.sales_config["business_mappings"]["quota_attainment"][
            "attained_stage"
        ]
        won_deals = tables["deals"][tables["deals"]["stage"] == won_stage]
        matched = 0
        unmatched = 0
        for target in tables["targets"].itertuples(index=False):
            matching = won_deals[
                (won_deals["rep_name"] == target.rep_name)
                & (won_deals["close_date"] >= target.period_start)
                & (won_deals["close_date"] < target.period_end)
            ]
            if matching.empty:
                unmatched += 1
            else:
                matched += 1
        return [
            _result(
                "targets.won_deal_attainment",
                matched > 0,
                f"{matched} target period(s) contain won deals",
                "no target period contains won deals",
            ),
            _result(
                "targets.zero_attainment",
                unmatched > 0,
                f"{unmatched} target period(s) have zero attainment",
                "no zero-attainment target period exists",
            ),
        ]

    def _validate_currency(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.sales_config["business_mappings"]["currency_code"]
        results: list[IntegrityCheckResult] = []
        for table_name in ("deals", "products", "targets"):
            actual = set(tables[table_name]["currency_code"].astype(str))
            results.append(
                _result(
                    f"{table_name}.currency_code",
                    actual == {expected},
                    f"all values use {expected}",
                    f"expected only {expected}, got {sorted(actual)}",
                )
            )
        return results


def _result(
    check_name: str,
    condition: bool,
    passed_message: str,
    failed_message: str,
) -> IntegrityCheckResult:
    return (
        passed(check_name, passed_message)
        if condition
        else failed(check_name, failed_message)
    )


def _count_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "no violations",
        f"{violation_count} violation(s)",
    )


def _temporal_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "chronology valid",
        f"{violation_count} temporal violation(s)",
    )


__all__ = ["SalesRelationalValidator"]
