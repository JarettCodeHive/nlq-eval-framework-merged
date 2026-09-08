"""Controlled imperfection injection for CRM engagement data."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.common.base import DeterministicGenerator
from generators.common.imperfections import count_from_pct
from generators.common.imperfections import inject_integer_outliers
from generators.common.imperfections import inject_nulls
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.generator import CRMBaseEntityGenerator


class CRMImperfectionInjector:
    """Inject deterministic defects without violating relational contracts."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMImperfectionInjector only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.crm_config = load_crm_config()

    @classmethod
    def for_profile(cls, profile: str) -> "CRMImperfectionInjector":
        """Create a CRM imperfection injector from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_imperfect_tables(self) -> dict[str, Any]:
        """Generate distributed CRM tables and inject configured imperfections."""

        distributed = CRMDistributionApplier(
            self.generator
        ).generate_distributed_tables()
        return self.apply_to_tables(distributed)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return imperfect copies while leaving distributed inputs unchanged."""

        CRMBaseEntityGenerator(self.generator)._validate_generated_tables(tables)
        imperfect = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }
        expected_contact_rows = len(tables["contacts"]) + count_from_pct(
            len(tables["contacts"]),
            float(self.config["duplicate_pct"]),
        )

        imperfect["contacts"] = self._inject_near_duplicate_contacts(
            imperfect["contacts"]
        )
        self._validate_core_invariants(imperfect, expected_contact_rows)

        imperfect["contact_campaigns"] = inject_nulls(
            imperfect["contact_campaigns"],
            "attribution_weight",
            self.generator.rng_for(
                "imperfections:contact_campaigns:attribution_weight_nulls"
            ),
            float(self.config["null_pct"]),
        )
        self._validate_core_invariants(imperfect, expected_contact_rows)

        outlier_target = self.crm_config["imperfection_targets"][
            "engagement_point_outliers"
        ]
        imperfect["interactions"] = inject_integer_outliers(
            imperfect["interactions"],
            "engagement_points",
            self.generator.rng_for(
                "imperfections:interactions:engagement_point_outliers"
            ),
            float(self.config["outlier_pct"]),
            min_outlier=int(outlier_target["minimum_value"]),
            max_outlier=int(outlier_target["maximum_value"]),
        )
        self._validate_core_invariants(imperfect, expected_contact_rows)

        self._inject_support_case_boundary_timestamps(imperfect["support_cases"])
        self._validate_core_invariants(imperfect, expected_contact_rows)
        self._validate_target_rates(imperfect, tables)
        return imperfect

    def _inject_near_duplicate_contacts(self, contacts: Any) -> Any:
        duplicate_count = count_from_pct(
            len(contacts), float(self.config["duplicate_pct"])
        )
        result = contacts.copy(deep=True)
        if duplicate_count == 0:
            return result

        rng = self.generator.rng_for("imperfections:contacts:near_duplicates")
        sampled_positions = rng.choice(
            contacts.index.to_numpy(),
            size=duplicate_count,
            replace=False,
        )
        duplicates = (
            contacts.loc[sampled_positions].copy(deep=True).reset_index(drop=True)
        )
        start_id = int(contacts["contact_id"].max()) + 1
        duplicates["contact_id"] = list(range(start_id, start_id + duplicate_count))
        duplicates["first_name"] = [
            _near_duplicate_first_name(value) for value in duplicates["first_name"]
        ]
        duplicates["email"] = [
            _near_duplicate_email(value) for value in duplicates["email"]
        ]

        pd = _require_pandas()
        return pd.concat([result, duplicates], ignore_index=True)

    def _inject_support_case_boundary_timestamps(self, support_cases: Any) -> None:
        """Inject each boundary opening and rederive dependent case timestamps."""

        boundaries = [
            datetime.combine(date.fromisoformat(str(value)), time.min)
            for value in self.config["boundary_dates"]
        ]
        if len(boundaries) > len(support_cases):
            raise ValueError("More boundary timestamps than support-case rows")

        resolved_statuses = set(
            self.crm_config["business_mappings"]["resolved_case_statuses"]
        )
        sla_hours = self.crm_config["business_mappings"]["sla_calendar_hours"]
        for position, boundary in enumerate(boundaries):
            row = support_cases.loc[position]
            original_opened = _parse_timestamp(row["opened_at"])
            creation_delta = _parse_timestamp(row["created_at"]) - original_opened
            resolution_delta = None
            if row["status"] in resolved_statuses:
                resolution_delta = (
                    _parse_timestamp(row["resolved_at"]) - original_opened
                )

            support_cases.at[position, "opened_at"] = _format_timestamp(boundary)
            support_cases.at[position, "sla_due_at"] = _format_timestamp(
                boundary + timedelta(hours=int(sla_hours[row["priority"]]))
            )
            support_cases.at[position, "created_at"] = _format_timestamp(
                boundary + creation_delta
            )
            support_cases.at[position, "resolved_at"] = (
                _format_timestamp(boundary + resolution_delta)
                if resolution_delta is not None
                else ""
            )

    def _validate_core_invariants(
        self,
        tables: dict[str, Any],
        expected_contact_rows: int,
    ) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Imperfect CRM table order differs from config")
        for table_name in self.settings.table_order:
            table = tables[table_name]
            table_config = self.crm_config["tables"][table_name]
            expected_columns = [field["name"] for field in table_config["fields"]]
            if table.columns.tolist() != expected_columns:
                raise ValueError(f"{table_name} columns changed after imperfections")
            expected_rows = (
                expected_contact_rows
                if table_name == "contacts"
                else self.generator.row_count(table_name)
            )
            if len(table) != expected_rows:
                raise ValueError(
                    f"{table_name} row count changed unexpectedly: "
                    f"expected {expected_rows}, got {len(table)}"
                )
            primary_key = table_config["primary_key"]
            primary_fields = (
                [primary_key] if isinstance(primary_key, str) else primary_key
            )
            if table.duplicated(primary_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _blank_count(table[field["name"]]):
                    raise ValueError(
                        f"Required field contains blanks: "
                        f"{table_name}.{field['name']}"
                    )
                if (
                    field.get("key") == "unique"
                    and table[field["name"]].duplicated().any()
                ):
                    raise ValueError(
                        f"{table_name}.{field['name']} contains duplicate values"
                    )

        accounts = tables["accounts"]
        contacts = tables["contacts"]
        campaigns = tables["campaigns"]
        memberships = tables["contact_campaigns"]
        interactions = tables["interactions"]
        support_cases = tables["support_cases"]
        account_ids = set(accounts["account_id"])
        contact_ids = set(contacts["contact_id"])
        campaign_ids = set(campaigns["campaign_id"])
        _assert_fk(contacts["account_id"], account_ids, "contacts.account_id")
        _assert_fk(
            memberships["contact_id"], contact_ids, "contact_campaigns.contact_id"
        )
        _assert_fk(
            memberships["campaign_id"], campaign_ids, "contact_campaigns.campaign_id"
        )
        _assert_fk(interactions["contact_id"], contact_ids, "interactions.contact_id")
        _assert_fk(interactions["account_id"], account_ids, "interactions.account_id")
        _assert_fk(
            interactions["campaign_id"], campaign_ids, "interactions.campaign_id"
        )
        _assert_fk(support_cases["account_id"], account_ids, "support_cases.account_id")
        _assert_fk(support_cases["contact_id"], contact_ids, "support_cases.contact_id")

        primary_counts = (
            memberships[memberships["is_primary_attribution"]]
            .groupby("contact_id")
            .size()
        )
        attribution = self.crm_config["business_mappings"]["attribution"]
        maximum_primary = int(attribution["maximum_primary_campaigns_per_contact"])
        if not primary_counts.empty and int(primary_counts.max()) > maximum_primary:
            raise ValueError("A contact exceeds the configured primary-campaign limit")
        minimum_weight = Decimal(attribution["minimum_weight"])
        maximum_weight = Decimal(attribution["maximum_weight"])
        expected_sum = Decimal(attribution["expected_sum_before_imperfections"])
        populated_weights = memberships.loc[
            memberships["attribution_weight"].astype(str).ne(""),
            "attribution_weight",
        ]
        if not populated_weights.map(
            lambda value: minimum_weight <= Decimal(str(value)) <= maximum_weight
        ).all():
            raise ValueError("Attribution weight is outside its valid range")
        for _, contact_memberships in memberships.groupby("contact_id"):
            weight_values = contact_memberships["attribution_weight"].astype(str)
            has_missing_weight = weight_values.eq("").any()
            known_total = sum(Decimal(value) for value in weight_values if value != "")
            if has_missing_weight and not minimum_weight <= known_total < expected_sum:
                raise ValueError(
                    "Incomplete contact attribution has an invalid known sum"
                )
            if not has_missing_weight and known_total != expected_sum:
                raise ValueError(
                    "Complete contact attribution weights have an invalid sum"
                )

        membership_pairs = set(
            zip(memberships["contact_id"], memberships["campaign_id"], strict=True)
        )
        campaign_windows = _campaign_windows(
            campaigns,
            self.settings.reference_today,
            int(
                self.crm_config["generation_rules"]["campaigns"][
                    "future_open_window_days"
                ]
            ),
        )
        for row in campaigns.itertuples(index=False):
            if row.end_date != "" and row.end_date < row.start_date:
                raise ValueError("Campaign end date precedes its start date")
        for row in memberships.itertuples(index=False):
            first_touch = _parse_timestamp(row.first_touch_at)
            last_touch = _parse_timestamp(row.last_touch_at)
            campaign_start, campaign_end = campaign_windows[row.campaign_id]
            if not campaign_start <= first_touch <= last_touch <= campaign_end:
                raise ValueError("Campaign membership touches are outside its window")

        attributed = interactions[interactions["campaign_id"].astype(str).ne("")]
        if any(
            (int(row.contact_id), int(row.campaign_id)) not in membership_pairs
            for row in attributed.itertuples(index=False)
        ):
            raise ValueError("An attributed interaction has no campaign membership")

        contact_accounts = dict(
            zip(contacts["contact_id"], contacts["account_id"], strict=True)
        )
        normal_points = self.crm_config["business_mappings"]["engagement_points"]
        outlier_minimum = int(
            self.crm_config["imperfection_targets"]["engagement_point_outliers"][
                "minimum_value"
            ]
        )
        for row in interactions.itertuples(index=False):
            if (
                row.account_id != ""
                and row.account_id != contact_accounts[row.contact_id]
            ):
                raise ValueError("Interaction account does not match its contact")
            if int(row.engagement_points) < 0:
                raise ValueError("Interaction engagement points cannot be negative")
            if (
                int(row.engagement_points) < outlier_minimum
                and int(row.engagement_points) != normal_points[row.engagement_type]
            ):
                raise ValueError("Non-outlier engagement points disagree with type")
            if _parse_timestamp(row.created_at) < _parse_timestamp(row.interaction_at):
                raise ValueError("Interaction creation precedes its event")
            if row.campaign_id != "":
                campaign_start, campaign_end = campaign_windows[row.campaign_id]
                interaction_at = _parse_timestamp(row.interaction_at)
                if not campaign_start <= interaction_at <= campaign_end:
                    raise ValueError("Interaction is outside its campaign window")

        resolved_statuses = set(
            self.crm_config["business_mappings"]["resolved_case_statuses"]
        )
        sla_hours = self.crm_config["business_mappings"]["sla_calendar_hours"]
        for row in support_cases.itertuples(index=False):
            if (
                row.contact_id != ""
                and contact_accounts[row.contact_id] != row.account_id
            ):
                raise ValueError("Support-case contact does not match its account")
            opened = _parse_timestamp(row.opened_at)
            expected_sla = opened + timedelta(hours=int(sla_hours[row.priority]))
            if _parse_timestamp(row.sla_due_at) != expected_sla:
                raise ValueError("Support-case SLA disagrees with opening and priority")
            resolved = row.status in resolved_statuses
            if resolved != (row.resolved_at != ""):
                raise ValueError("Support-case status and resolved_at disagree")
            if row.resolved_at and _parse_timestamp(row.resolved_at) < opened:
                raise ValueError("Support-case resolution precedes opening")
            if _parse_timestamp(row.created_at) < opened:
                raise ValueError("Support-case creation precedes opening")
        currency_code = self.crm_config["business_mappings"]["currency_code"]
        if set(campaigns["currency_code"].astype(str)) != {currency_code}:
            raise ValueError("CRM campaigns must remain USD-only")

    def _validate_target_rates(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        duplicate_count = count_from_pct(
            len(source_tables["contacts"]), float(self.config["duplicate_pct"])
        )
        if len(tables["contacts"]) - len(source_tables["contacts"]) != duplicate_count:
            raise ValueError("Contact near-duplicate count differs from config")

        expected_nulls = count_from_pct(
            len(source_tables["contact_campaigns"]),
            float(self.config["null_pct"]),
        )
        actual_nulls = _blank_count(tables["contact_campaigns"]["attribution_weight"])
        if actual_nulls != expected_nulls:
            raise ValueError(
                "Attribution-weight NULL count differs from config: "
                f"expected {expected_nulls}, got {actual_nulls}"
            )

        target = self.crm_config["imperfection_targets"]["engagement_point_outliers"]
        expected_outliers = count_from_pct(
            len(source_tables["interactions"]),
            float(self.config["outlier_pct"]),
        )
        points = tables["interactions"]["engagement_points"].astype(int)
        actual_outliers = int(points.ge(int(target["minimum_value"])).sum())
        if actual_outliers != expected_outliers:
            raise ValueError(
                "Engagement-point outlier count differs from config: "
                f"expected {expected_outliers}, got {actual_outliers}"
            )
        if points.max() > int(target["maximum_value"]):
            raise ValueError("Engagement-point outlier exceeds configured maximum")

        expected_boundaries = {
            f"{date.fromisoformat(str(value)).isoformat()}T00:00:00"
            for value in self.config["boundary_dates"]
        }
        actual_openings = set(tables["support_cases"]["opened_at"].astype(str))
        if not expected_boundaries.issubset(actual_openings):
            raise ValueError("Not every support-case boundary timestamp was injected")


def _near_duplicate_first_name(value: object) -> str:
    text = str(value).strip()
    if not text:
        return "Duplicate"
    return _adjacent_transposition(text, preserve_title_case=True)


def _near_duplicate_email(value: object) -> str:
    """Introduce a deterministic typo in an email address's local part."""

    text = str(value).strip()
    local, separator, domain = text.partition("@")
    if not separator or not local or not domain:
        return text
    return f"{_adjacent_transposition(local)}@{domain}"


def _adjacent_transposition(text: str, preserve_title_case: bool = False) -> str:
    """Swap the last unequal adjacent characters, or repeat the final one."""

    if len(text) == 1:
        return text * 2
    characters = list(text)
    for position in range(len(characters) - 2, -1, -1):
        if characters[position].casefold() == characters[position + 1].casefold():
            continue
        characters[position], characters[position + 1] = (
            characters[position + 1],
            characters[position],
        )
        transposed = "".join(characters)
        if preserve_title_case and text.istitle():
            return transposed.capitalize()
        return transposed
    return f"{text}{text[-1]}"


def _assert_fk(values: Any, parent_ids: set[Any], label: str) -> None:
    invalid = {
        value
        for value in values.tolist()
        if str(value) != "" and value not in parent_ids
    }
    if invalid:
        raise ValueError(f"{label} contains invalid FK values: {sorted(invalid)[:5]}")


def _campaign_windows(
    campaigns: Any,
    reference_today: date,
    future_open_window_days: int,
) -> dict[int, tuple[datetime, datetime]]:
    windows: dict[int, tuple[datetime, datetime]] = {}
    for row in campaigns[["campaign_id", "start_date", "end_date"]].itertuples(
        index=False
    ):
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


def _blank_count(values: Any) -> int:
    return int(values.astype(str).eq("").sum())


def _parse_timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for CRM imperfections") from exc
    return pd
