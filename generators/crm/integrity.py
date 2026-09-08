"""Relational validity checks for CRM engagement datasets."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.common.base import DeterministicGenerator
from generators.common.imperfections import count_from_pct
from generators.common.integrity import IntegrityCheckResult
from generators.common.integrity import assert_all_passed
from generators.common.integrity import empty_count
from generators.common.integrity import failed
from generators.common.integrity import non_empty_values
from generators.common.integrity import passed
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector


class CRMRelationalValidator:
    """Report relational and temporal validity after CRM imperfections."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMRelationalValidator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.crm_config = load_crm_config()

    @classmethod
    def for_profile(cls, profile: str) -> "CRMRelationalValidator":
        """Create a CRM relational validator from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate imperfect CRM tables and validate them."""

        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return all applicable relational checks for CRM tables."""

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
        results.extend(self._validate_contact_campaigns(tables))
        results.extend(self._validate_interactions(tables))
        results.extend(self._validate_support_cases(tables))
        results.extend(self._validate_temporal_rules(tables))
        results.extend(self._validate_status_null_rules(tables))
        results.extend(self._validate_currency(tables))
        return results

    def validate_or_raise(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate tables and raise a combined error for failed checks."""

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
                for field in self.crm_config["tables"][table_name]["fields"]
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
        expected_counts["contacts"] += count_from_pct(
            expected_counts["contacts"],
            float(self.settings.imperfections["duplicate_pct"]),
        )
        return [
            _result(
                f"{table_name}.row_count",
                len(tables[table_name]) == expected,
                f"row count {expected}",
                f"expected {expected}, got {len(tables[table_name])}",
            )
            for table_name, expected in expected_counts.items()
        ]

    def _validate_keys(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            table_config = self.crm_config["tables"][table_name]
            primary_key = table_config["primary_key"]
            key_columns = (
                [primary_key] if isinstance(primary_key, str) else list(primary_key)
            )
            blank_columns = [
                column
                for column in key_columns
                if empty_count(tables[table_name][column]) > 0
            ]
            duplicate_count = int(tables[table_name].duplicated(key_columns).sum())
            results.append(
                _result(
                    f"{table_name}.primary_key",
                    not blank_columns and duplicate_count == 0,
                    "primary key populated and unique",
                    f"blank columns={blank_columns}, duplicate rows={duplicate_count}",
                )
            )
            for field in table_config["fields"]:
                if field.get("key") != "unique":
                    continue
                column = field["name"]
                duplicates = int(tables[table_name][column].duplicated().sum())
                results.append(
                    _result(
                        f"{table_name}.{column}.unique",
                        duplicates == 0,
                        "unique values",
                        f"{duplicates} duplicate value(s)",
                    )
                )
        return results

    def _validate_required_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.crm_config["tables"][table_name]["fields"]:
                if field["nullable"]:
                    continue
                column = field["name"]
                blanks = empty_count(tables[table_name][column])
                results.append(
                    _result(
                        f"{table_name}.{column}.not_null",
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
        for relationship in self.crm_config["relationships"]:
            parent_table = relationship["parent_table"]
            parent_field = relationship["parent_field"]
            child_table = relationship["child_table"]
            child_field = relationship["child_field"]
            allowed = non_empty_values(tables[parent_table][parent_field])
            actual = non_empty_values(tables[child_table][child_field])
            invalid = sorted(actual - allowed)
            label = f"{child_table}.{child_field}.fk"
            results.append(
                _result(
                    label,
                    not invalid,
                    "all non-empty references valid",
                    f"invalid references: {invalid[:5]}",
                )
            )
        return results

    def _validate_contact_campaigns(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        memberships = tables["contact_campaigns"]
        duplicate_pairs = int(
            memberships.duplicated(["contact_id", "campaign_id"]).sum()
        )
        campaigns_per_contact = memberships.groupby("contact_id")[
            "campaign_id"
        ].nunique()
        contacts_per_campaign = memberships.groupby("campaign_id")[
            "contact_id"
        ].nunique()
        primary_counts = (
            memberships[_true_mask(memberships["is_primary_attribution"])]
            .groupby("contact_id")
            .size()
        )
        max_primary = 0 if primary_counts.empty else int(primary_counts.max())
        attribution = self.crm_config["business_mappings"]["attribution"]
        minimum_weight = Decimal(attribution["minimum_weight"])
        maximum_weight = Decimal(attribution["maximum_weight"])
        expected_sum = Decimal(attribution["expected_sum_before_imperfections"])
        maximum_primary = int(attribution["maximum_primary_campaigns_per_contact"])

        populated_weights = memberships.loc[
            memberships["attribution_weight"].astype(str).ne(""),
            "attribution_weight",
        ]
        invalid_weights = sum(
            not minimum_weight <= Decimal(str(value)) <= maximum_weight
            for value in populated_weights
        )
        invalid_sums = 0
        for _, rows in memberships.groupby("contact_id"):
            values = rows["attribution_weight"].astype(str)
            has_missing = values.eq("").any()
            known_sum = sum(Decimal(value) for value in values if value != "")
            valid_sum = (
                minimum_weight <= known_sum < expected_sum
                if has_missing
                else known_sum == expected_sum
            )
            invalid_sums += not valid_sum

        return [
            _result(
                "contact_campaigns.composite_key",
                duplicate_pairs == 0,
                "contact/campaign pairs unique",
                f"{duplicate_pairs} duplicate pair(s)",
            ),
            _result(
                "contact_campaigns.contact_many_campaigns",
                not campaigns_per_contact.empty
                and int(campaigns_per_contact.max()) >= 2,
                "at least one contact belongs to multiple campaigns",
                "no contact belongs to multiple campaigns",
            ),
            _result(
                "contact_campaigns.campaign_many_contacts",
                not contacts_per_campaign.empty
                and int(contacts_per_campaign.max()) >= 2,
                "at least one campaign has multiple contacts",
                "no campaign has multiple contacts",
            ),
            _result(
                "contact_campaigns.primary_per_contact",
                max_primary <= maximum_primary,
                "primary attribution count is within the configured limit",
                f"maximum primary attributions for one contact is {max_primary}",
            ),
            _result(
                "contact_campaigns.attribution_range",
                invalid_weights == 0,
                "all populated weights are within the configured range",
                f"{invalid_weights} weight(s) outside the valid range",
            ),
            _result(
                "contact_campaigns.attribution_sums",
                invalid_sums == 0,
                "complete and incomplete attribution sums valid",
                f"{invalid_sums} contact(s) have invalid attribution sums",
            ),
        ]

    def _validate_interactions(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        contacts = tables["contacts"][["contact_id", "account_id"]]
        interactions = tables["interactions"]
        merged = interactions.merge(
            contacts,
            on="contact_id",
            how="left",
            suffixes=("_interaction", "_contact"),
        )
        account_mismatches = int(
            (
                merged["account_id_interaction"].astype(str).ne("")
                & (
                    merged["account_id_interaction"].astype(str)
                    != merged["account_id_contact"].astype(str)
                )
            ).sum()
        )

        memberships = tables["contact_campaigns"][["contact_id", "campaign_id"]]
        attributed = interactions[interactions["campaign_id"].astype(str).ne("")].copy()
        attributed["campaign_id"] = attributed["campaign_id"].astype(str)
        membership_keys = {
            (str(row.contact_id), str(row.campaign_id))
            for row in memberships.itertuples(index=False)
        }
        missing_memberships = sum(
            (str(row.contact_id), str(row.campaign_id)) not in membership_keys
            for row in attributed.itertuples(index=False)
        )
        return [
            _consistency_result(
                "interactions.contact_account_consistency",
                account_mismatches,
            ),
            _result(
                "interactions.contact_campaign_membership",
                missing_memberships == 0,
                "every attributed interaction has a membership",
                f"{missing_memberships} attributed interaction(s) lack membership",
            ),
        ]

    def _validate_support_cases(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        contacts = tables["contacts"][["contact_id", "account_id"]]
        support_cases = tables["support_cases"]
        merged = support_cases.merge(
            contacts,
            on="contact_id",
            how="left",
            suffixes=("_case", "_contact"),
        )
        mismatches = int(
            (
                merged["contact_id"].astype(str).ne("")
                & (
                    merged["account_id_case"].astype(str)
                    != merged["account_id_contact"].astype(str)
                )
            ).sum()
        )
        return [
            _consistency_result(
                "support_cases.contact_account_consistency",
                mismatches,
            )
        ]

    def _validate_temporal_rules(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        campaigns = tables["campaigns"]
        memberships = tables["contact_campaigns"]
        interactions = tables["interactions"]
        support_cases = tables["support_cases"]
        campaign_windows = _campaign_windows(
            campaigns,
            self.settings.reference_today,
            int(
                self.crm_config["generation_rules"]["campaigns"][
                    "future_open_window_days"
                ]
            ),
        )

        invalid_campaigns = 0
        for row in campaigns.itertuples(index=False):
            start = date.fromisoformat(str(row.start_date))
            if row.end_date != "" and date.fromisoformat(str(row.end_date)) < start:
                invalid_campaigns += 1
            if _parse_timestamp(row.created_at) > datetime.combine(start, time.min):
                invalid_campaigns += 1

        invalid_touches = 0
        for row in memberships.itertuples(index=False):
            first_blank = str(row.first_touch_at) == ""
            last_blank = str(row.last_touch_at) == ""
            if first_blank != last_blank:
                invalid_touches += 1
                continue
            if first_blank:
                continue
            start, end = campaign_windows[int(row.campaign_id)]
            first = _parse_timestamp(row.first_touch_at)
            last = _parse_timestamp(row.last_touch_at)
            invalid_touches += not start <= first <= last <= end

        invalid_interaction_times = 0
        for row in interactions.itertuples(index=False):
            interaction_at = _parse_timestamp(row.interaction_at)
            if _parse_timestamp(row.created_at) < interaction_at:
                invalid_interaction_times += 1
            if row.campaign_id != "":
                start, end = campaign_windows[int(row.campaign_id)]
                invalid_interaction_times += not start <= interaction_at <= end

        invalid_case_times = 0
        sla_hours = self.crm_config["business_mappings"]["sla_calendar_hours"]
        for row in support_cases.itertuples(index=False):
            opened = _parse_timestamp(row.opened_at)
            expected_sla = opened + timedelta(hours=int(sla_hours[row.priority]))
            invalid_case_times += _parse_timestamp(row.sla_due_at) != expected_sla
            invalid_case_times += _parse_timestamp(row.created_at) < opened
            if row.resolved_at != "":
                invalid_case_times += _parse_timestamp(row.resolved_at) < opened

        return [
            _temporal_result("campaigns.temporal", invalid_campaigns),
            _temporal_result("contact_campaigns.temporal", invalid_touches),
            _temporal_result("interactions.temporal", invalid_interaction_times),
            _temporal_result("support_cases.temporal", invalid_case_times),
        ]

    def _validate_status_null_rules(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        campaigns = tables["campaigns"]
        status_rules = self.crm_config["business_mappings"]["campaign_status_rules"]
        requires_end_date = set(status_rules["requires_end_date"])
        open_ended = set(self.crm_config["domain_values"]["campaign_statuses"]) - (
            requires_end_date
        )
        campaign_end_mismatches = sum(
            (row.status in requires_end_date and row.end_date == "")
            or (row.status in open_ended and row.end_date != "")
            for row in campaigns.itertuples(index=False)
        )

        resolved_statuses = set(
            self.crm_config["business_mappings"]["resolved_case_statuses"]
        )
        case_resolution_mismatches = sum(
            (row.status in resolved_statuses) != (row.resolved_at != "")
            for row in tables["support_cases"].itertuples(index=False)
        )
        return [
            _result(
                "campaigns.status_end_date",
                campaign_end_mismatches == 0,
                "campaign status and end-date NULL rules agree",
                f"{campaign_end_mismatches} campaign mismatch(es)",
            ),
            _result(
                "support_cases.status_resolved_at",
                case_resolution_mismatches == 0,
                "case status and resolved timestamp agree",
                f"{case_resolution_mismatches} support-case mismatch(es)",
            ),
        ]

    def _validate_currency(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        currencies = set(tables["campaigns"]["currency_code"].astype(str))
        expected_currency = self.crm_config["business_mappings"]["currency_code"]
        return [
            _result(
                "campaigns.currency_code",
                currencies == {expected_currency},
                f"all campaign budgets use {expected_currency}",
                f"expected only {expected_currency}, got {sorted(currencies)}",
            )
        ]


def _campaign_windows(
    campaigns: Any,
    reference_today: date,
    future_open_window_days: int,
) -> dict[int, tuple[datetime, datetime]]:
    windows: dict[int, tuple[datetime, datetime]] = {}
    for row in campaigns.itertuples(index=False):
        start = date.fromisoformat(str(row.start_date))
        if row.end_date == "":
            end = max(start, reference_today)
            if start > reference_today:
                end = start + timedelta(days=future_open_window_days)
        else:
            end = date.fromisoformat(str(row.end_date))
        windows[int(row.campaign_id)] = (
            datetime.combine(start, time.min),
            datetime.combine(end, time(23, 59, 59)),
        )
    return windows


def _result(
    check_name: str,
    condition: bool,
    passed_message: str,
    failed_message: str,
) -> IntegrityCheckResult:
    if condition:
        return passed(check_name, passed_message)
    return failed(check_name, failed_message)


def _consistency_result(
    check_name: str,
    mismatch_count: int,
) -> IntegrityCheckResult:
    return _result(
        check_name,
        mismatch_count == 0,
        "no mismatches",
        f"{mismatch_count} mismatched row(s)",
    )


def _temporal_result(
    check_name: str,
    violation_count: int,
) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "chronology valid",
        f"{violation_count} temporal violation(s)",
    )


def _true_mask(values: Any) -> Any:
    return values.astype(str).str.lower().isin({"true", "1"})


def _parse_timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))
