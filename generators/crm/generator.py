"""CRM engagement-schema column and deterministic stream contracts."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
import re
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.progress import ProgressReporter
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config


_CRM_CONFIG = load_crm_config()
CRM_COLUMN_CONTRACTS = {
    table_name: [field["name"] for field in _CRM_CONFIG["tables"][table_name]["fields"]]
    for table_name in _CRM_CONFIG["table_order"]
}


class CRMBaseEntityGenerator:
    """Generate clean, deterministic CRM engagement entities."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMBaseEntityGenerator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.crm_config = load_crm_config()
        self.generation_rules = self.crm_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "CRMBaseEntityGenerator":
        """Create a validated CRM base generator for a profile."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings), progress=progress)

    def generate_tables(self) -> dict[str, Any]:
        """Generate all clean CRM tables in dependency order."""

        accounts = self._generate_and_report("accounts", self.generate_accounts)
        contacts = self._generate_and_report(
            "contacts", lambda: self.generate_contacts(accounts)
        )
        campaigns = self._generate_and_report("campaigns", self.generate_campaigns)
        contact_campaigns = self._generate_and_report(
            "contact_campaigns",
            lambda: self.generate_contact_campaigns(contacts, campaigns),
        )
        interactions = self._generate_and_report(
            "interactions",
            lambda: self.generate_interactions(
                contacts,
                campaigns,
                contact_campaigns,
            ),
        )
        support_cases = self._generate_and_report(
            "support_cases", lambda: self.generate_support_cases(accounts, contacts)
        )
        tables = {
            "accounts": accounts,
            "contacts": contacts,
            "campaigns": campaigns,
            "contact_campaigns": contact_campaigns,
            "interactions": interactions,
            "support_cases": support_cases,
        }
        self._report("Validating base CRM tables")
        self._validate_generated_tables(tables)
        self._report("Base CRM generation complete")
        return tables

    def _generate_and_report(self, table_name: str, generate: Any) -> Any:
        """Generate one table and report its completed shape when enabled."""

        self._report(f"Generating {table_name}")
        table = generate()
        if self.progress is not None:
            self.progress.report_table(table_name, table)
        return table

    def _report(self, message: str) -> None:
        """Report progress when the caller supplied a reporter."""

        if self.progress is not None:
            self.progress.report(message)

    def generate_accounts(self) -> Any:
        """Generate the base CRM accounts dimension table.

        Each row represents one synthetic customer organization with its size,
        industry, region, customer tier, active state, and creation timestamp.
        Accounts are the root customer entities referenced by contacts,
        interactions, and support cases.

        Returns:
            A DataFrame matching the configured ``accounts`` column contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("accounts")
        rng = self.generator.rng_for("crm:accounts:attributes")
        date_rng = self.generator.rng_for("crm:accounts:created_at")
        fake = self.generator.faker_for("crm:accounts:faker")
        rules = self.generation_rules["accounts"]
        date_windows = self.generation_rules["date_windows"]
        industries = self.crm_config["domain_values"]["industries"]
        regions = self.crm_config["domain_values"]["regions"]
        customer_tiers = self.crm_config["domain_values"]["customer_tiers"]
        account_size = rules["account_size"]
        active_probability = float(rules["active_probability"])
        # Bound creation history by the fixed reference date for reproducible queries.
        created_at = _random_timestamp_strings(
            date_rng,
            count,
            _configured_datetime(date_windows["account_created_start"]),
            _reference_datetime(self.settings.reference_today),
        )

        return pd.DataFrame(
            {
                "account_id": self.generator.make_integer_ids(count),
                # Normalize Faker output while retaining synthetic company-like names.
                "account_name": [
                    _normalized_company_name(fake.company()) for _ in range(count)
                ],
                "account_size": rng.integers(
                    int(account_size["minimum"]),
                    int(account_size["maximum"]) + 1,
                    size=count,
                ).tolist(),
                "industry": rng.choice(industries, size=count).tolist(),
                "region": rng.choice(regions, size=count).tolist(),
                "customer_tier": rng.choice(
                    customer_tiers,
                    size=count,
                    p=_ordered_weights(
                        customer_tiers,
                        rules["customer_tier_weights"],
                    ),
                ).tolist(),
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[active_probability, 1 - active_probability],
                ).tolist(),
                "created_at": created_at,
            },
            columns=CRM_COLUMN_CONTRACTS["accounts"],
        )

    def generate_contacts(self, accounts: Any) -> Any:
        """Generate the base CRM contacts dimension table.

        Each row represents one synthetic person who may be associated with an
        existing account. Populated account references are always valid, while
        selected contacts remain unassigned to support account-to-contact LEFT
        JOIN scenarios. Contact creation cannot predate its linked account.

        Args:
            accounts: Generated accounts used for membership and date validity.

        Returns:
            A DataFrame matching the configured ``contacts`` column contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("contacts")
        rng = self.generator.rng_for("crm:contacts:attributes")
        date_rng = self.generator.rng_for("crm:contacts:created_at")
        fake = self.generator.faker_for("crm:contacts:faker")
        rules = self.generation_rules["contacts"]
        date_windows = self.generation_rules["date_windows"]
        account_ids = [int(value) for value in accounts["account_id"].tolist()]
        # Restrict membership so some accounts intentionally remain without contacts.
        eligible_accounts = account_ids[
            : max(1, int(len(account_ids) * float(rules["eligible_account_fraction"])))
        ]
        account_created_at = dict(
            zip(accounts["account_id"], accounts["created_at"], strict=True)
        )
        selected_accounts: list[int | str] = []
        for _ in range(count):
            # Empty membership creates valid unassigned contacts for LEFT JOIN tests.
            if float(rng.random()) < float(rules["unassigned_account_probability"]):
                selected_accounts.append("")
            else:
                selected_accounts.append(int(rng.choice(eligible_accounts)))
        if selected_accounts:
            selected_accounts[-1] = ""

        first_names = [fake.first_name() for _ in range(count)]
        last_names = [fake.last_name() for _ in range(count)]
        titles = self.crm_config["domain_values"]["contact_titles"]
        reference = _reference_datetime(self.settings.reference_today)
        # Linked contacts cannot be created before their parent account.
        created_at = [
            _random_timestamp_string(
                date_rng,
                (
                    _as_datetime(account_created_at[account_id])
                    if account_id != ""
                    else _configured_datetime(
                        date_windows["unlinked_contact_created_start"]
                    )
                ),
                reference,
            )
            for account_id in selected_accounts
        ]

        return pd.DataFrame(
            {
                "contact_id": self.generator.make_integer_ids(count),
                "account_id": selected_accounts,
                "first_name": first_names,
                "last_name": last_names,
                "email": [
                    _synthetic_email(first_name, last_name, contact_id)
                    for contact_id, (first_name, last_name) in enumerate(
                        zip(first_names, last_names, strict=True),
                        start=1,
                    )
                ],
                "address": [_single_line_address(fake) for _ in range(count)],
                "title": rng.choice(titles, size=count).tolist(),
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[
                        float(rules["active_probability"]),
                        1 - float(rules["active_probability"]),
                    ],
                ).tolist(),
                "created_at": created_at,
            },
            columns=CRM_COLUMN_CONTRACTS["contacts"],
        )

    def generate_campaigns(self) -> Any:
        """Generate the base CRM campaigns dimension table.

        Each row represents one USD campaign with a configured type, primary
        channel, status, budget, and lifecycle dates. Status-aware rules keep
        future and historical campaigns in coherent date windows, determine
        when an end date is required, and preserve both populated and missing
        budgets for downstream benchmark questions.

        Returns:
            A DataFrame matching the configured ``campaigns`` column contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("campaigns")
        rng = self.generator.rng_for("crm:campaigns:attributes")
        date_rng = self.generator.rng_for("crm:campaigns:dates")
        values = self.crm_config["domain_values"]
        mappings = self.crm_config["business_mappings"]
        rules = self.generation_rules["campaigns"]
        date_windows = self.generation_rules["date_windows"]
        campaign_statuses = values["campaign_statuses"]
        statuses = rng.choice(
            campaign_statuses,
            size=count,
            p=_ordered_weights(campaign_statuses, rules["status_weights"]),
        ).tolist()
        _ensure_required_coverage(statuses, rules["required_status_coverage"])

        reference_date = self.settings.reference_today
        reference = _reference_datetime(reference_date)
        status_rules = mappings["campaign_status_rules"]
        future_start_statuses = set(status_rules["future_start"])
        requires_end_date_statuses = set(status_rules["requires_end_date"])
        future_offsets = rules["future_start_offset_days"]
        historical_durations = rules["historical_end_duration_days"]
        future_durations = rules["future_end_duration_days"]
        creation_lead = rules["creation_lead_days"]
        start_dates: list[str] = []
        end_dates: list[str] = []
        created_at: list[str] = []
        for status in statuses:
            # Campaign status determines whether its lifecycle is future or historical.
            if status in future_start_statuses:
                start = _random_date(
                    date_rng,
                    reference_date + timedelta(days=int(future_offsets["minimum"])),
                    reference_date + timedelta(days=int(future_offsets["maximum"])),
                )
            else:
                start = _random_date(
                    date_rng,
                    date.fromisoformat(date_windows["campaign_historical_start"]),
                    reference_date,
                )
            if status in requires_end_date_statuses:
                duration = (
                    future_durations
                    if status in future_start_statuses
                    else historical_durations
                )
                proposed_end = start + timedelta(
                    days=int(
                        date_rng.integers(
                            int(duration["minimum"]),
                            int(duration["maximum"]) + 1,
                        )
                    )
                )
                end: date | None = (
                    proposed_end
                    if status in future_start_statuses
                    else min(proposed_end, reference_date)
                )
            else:
                end = None
            start_dates.append(start.isoformat())
            end_dates.append(end.isoformat() if end else "")
            campaign_created = datetime.combine(start, time.min) - timedelta(
                days=int(
                    date_rng.integers(
                        int(creation_lead["minimum"]),
                        int(creation_lead["maximum"]) + 1,
                    )
                )
            )
            created_at.append(_format_timestamp(min(campaign_created, reference)))

        campaign_types = rng.choice(values["campaign_types"], size=count).tolist()
        channels = rng.choice(values["campaign_channels"], size=count).tolist()
        budget_rules = rules["budget"]
        budgets = [
            (
                ""
                if float(rng.random()) < float(budget_rules["null_probability"])
                else f"{int(rng.integers(int(budget_rules['minimum_amount']), int(budget_rules['maximum_amount']) + 1)):.2f}"
            )
            for _ in range(count)
        ]
        # Guarantee both populated and missing budgets for downstream test cases.
        if count and budgets[0] == "":
            budgets[0] = f"{int(budget_rules['minimum_amount']):.2f}"
        if count > 1:
            budgets[-1] = ""

        return pd.DataFrame(
            {
                "campaign_id": self.generator.make_integer_ids(count),
                "campaign_name": [
                    f"{campaign_type} {channel} Campaign {campaign_id:05d}"
                    for campaign_id, (campaign_type, channel) in enumerate(
                        zip(campaign_types, channels, strict=True),
                        start=1,
                    )
                ],
                "campaign_type": campaign_types,
                "primary_channel": channels,
                "status": statuses,
                "start_date": start_dates,
                "end_date": end_dates,
                "budget_amount": budgets,
                "currency_code": [mappings["currency_code"]] * count,
                "created_at": created_at,
            },
            columns=CRM_COLUMN_CONTRACTS["campaigns"],
        )

    def generate_contact_campaigns(self, contacts: Any, campaigns: Any) -> Any:
        """Generate the base contact-to-campaign many-to-many bridge table.

        Each row represents one unique campaign membership for a valid contact
        and campaign. Every parent row is covered at least once, touch dates
        remain inside the campaign window, and each contact receives one primary
        attribution with fixed-scale weights that total the configured value.

        Args:
            contacts: Generated contacts that can participate in campaigns.
            campaigns: Generated campaigns available for membership.

        Returns:
            A DataFrame matching the configured ``contact_campaigns`` column
            contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("contact_campaigns")
        contact_ids = [int(value) for value in contacts["contact_id"].tolist()]
        campaign_ids = [int(value) for value in campaigns["campaign_id"].tolist()]
        if count < max(len(contact_ids), len(campaign_ids)):
            raise ValueError(
                "contact_campaigns row target must cover every contact and campaign"
            )
        capacity = len(contact_ids) * len(campaign_ids)
        if count > capacity:
            raise ValueError(
                "contact_campaigns row target exceeds unique pair capacity: "
                f"target={count}, capacity={capacity}"
            )

        pair_rng = self.generator.rng_for("crm:contact_campaigns:pairs")
        attribute_rng = self.generator.rng_for("crm:contact_campaigns:attributes")
        date_rng = self.generator.rng_for("crm:contact_campaigns:dates")
        pairs: list[tuple[int, int]] = []
        pair_set: set[tuple[int, int]] = set()
        # Seed deterministic coverage before filling the remaining unique pairs.
        for index, contact_id in enumerate(contact_ids):
            _append_pair(
                pairs,
                pair_set,
                (contact_id, campaign_ids[index % len(campaign_ids)]),
            )
        for index, campaign_id in enumerate(campaign_ids):
            _append_pair(
                pairs,
                pair_set,
                (contact_ids[index % len(contact_ids)], campaign_id),
            )
        while len(pairs) < count:
            remaining = count - len(pairs)
            batch_size = max(remaining * 2, 32)
            sampled_contacts = pair_rng.choice(contact_ids, size=batch_size)
            sampled_campaigns = pair_rng.choice(campaign_ids, size=batch_size)
            for contact_id, campaign_id in zip(
                sampled_contacts,
                sampled_campaigns,
                strict=True,
            ):
                _append_pair(
                    pairs,
                    pair_set,
                    (int(contact_id), int(campaign_id)),
                )
                if len(pairs) == count:
                    break

        campaign_windows = _campaign_windows(
            campaigns,
            self.settings.reference_today,
            int(self.generation_rules["campaigns"]["future_open_window_days"]),
        )
        member_statuses = self.crm_config["domain_values"]["campaign_member_statuses"]
        touches: list[tuple[str, str]] = []
        for _, campaign_id in pairs:
            start, end = campaign_windows[campaign_id]
            first_touch = _random_datetime(date_rng, start, end)
            last_touch = _random_datetime(date_rng, first_touch, end)
            touches.append(
                (_format_timestamp(first_touch), _format_timestamp(last_touch))
            )

        pair_counts: dict[int, int] = {}
        pair_positions: dict[int, int] = {}
        for contact_id, _ in pairs:
            pair_counts[contact_id] = pair_counts.get(contact_id, 0) + 1
        weights: list[str] = []
        primary_flags: list[bool] = []
        attribution = self.crm_config["business_mappings"]["attribution"]
        weight_scale = int(attribution["weight_scale"])
        multiplier = 10**weight_scale
        expected_weight_units = int(
            Decimal(attribution["expected_sum_before_imperfections"]) * multiplier
        )
        # Divide integer weight units to avoid floating-point attribution drift.
        for contact_id, _ in pairs:
            position = pair_positions.get(contact_id, 0)
            pair_positions[contact_id] = position + 1
            units, remainder = divmod(
                expected_weight_units,
                pair_counts[contact_id],
            )
            weight_units = units + (1 if position < remainder else 0)
            weights.append(f"{weight_units / multiplier:.{weight_scale}f}")
            primary_flags.append(position == 0)

        return pd.DataFrame(
            {
                "contact_id": [contact_id for contact_id, _ in pairs],
                "campaign_id": [campaign_id for _, campaign_id in pairs],
                "member_status": attribute_rng.choice(
                    member_statuses, size=count
                ).tolist(),
                "first_touch_at": [first for first, _ in touches],
                "last_touch_at": [last for _, last in touches],
                "attribution_weight": weights,
                "is_primary_attribution": primary_flags,
                "created_at": [first for first, _ in touches],
            },
            columns=CRM_COLUMN_CONTRACTS["contact_campaigns"],
        )

    def generate_interactions(
        self,
        contacts: Any,
        campaigns: Any,
        contact_campaigns: Any,
    ) -> Any:
        """Generate the base CRM interactions fact table.

        Each row represents one engagement event associated with a valid
        contact. An interaction may reference the contact's account and may be
        attributed to a campaign, but campaign attribution is allowed only when
        that contact/campaign membership exists in ``contact_campaigns``.

        Organic interactions have no campaign attribution. Engagement type
        determines channel and points, and event and creation timestamps remain
        within the applicable campaign or organic activity window.

        Args:
            contacts: Generated contacts and their optional account membership.
            campaigns: Generated campaigns and their valid activity windows.
            contact_campaigns: Valid memberships used for campaign attribution.

        Returns:
            A DataFrame matching the configured ``interactions`` column contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("interactions")
        relationship_rng = self.generator.rng_for("crm:interactions:relationships")
        attribute_rng = self.generator.rng_for("crm:interactions:attributes")
        date_rng = self.generator.rng_for("crm:interactions:dates")
        reference = _reference_datetime(self.settings.reference_today)
        rules = self.generation_rules["interactions"]
        date_windows = self.generation_rules["date_windows"]
        contact_accounts = {
            int(row.contact_id): row.account_id
            for row in contacts[["contact_id", "account_id"]].itertuples(index=False)
        }
        campaign_windows = _campaign_windows(
            campaigns,
            self.settings.reference_today,
            int(self.generation_rules["campaigns"]["future_open_window_days"]),
        )
        attributed_campaign_limit = max(
            1,
            int(len(campaigns) * float(rules["attributed_campaign_fraction"])),
        )
        attributed_campaign_ids = set(
            int(value)
            for value in campaigns.iloc[:attributed_campaign_limit]["campaign_id"]
            if campaign_windows[int(value)][0] <= reference
        )
        # Only real bridge memberships can be used for campaign attribution.
        memberships: dict[int, list[int]] = {}
        for row in contact_campaigns[["contact_id", "campaign_id"]].itertuples(
            index=False
        ):
            campaign_id = int(row.campaign_id)
            if campaign_id in attributed_campaign_ids:
                memberships.setdefault(int(row.contact_id), []).append(campaign_id)
        eligible_contacts = sorted(memberships)
        selected_contact_pool = eligible_contacts[
            : max(
                1,
                int(len(eligible_contacts) * float(rules["eligible_contact_fraction"])),
            )
        ]
        # Limiting the pool deliberately leaves contacts without interactions.
        if not selected_contact_pool:
            raise ValueError("No contacts can support generated interactions")

        mappings = self.crm_config["business_mappings"]
        engagement_types = self.crm_config["domain_values"]["engagement_types"]
        directions = self.crm_config["domain_values"]["interaction_directions"]
        selected_contacts = relationship_rng.choice(
            selected_contact_pool,
            size=count,
        ).tolist()
        organic_draws = relationship_rng.random(count)
        campaign_draws = relationship_rng.random(count)
        account_null_draws = relationship_rng.random(count)
        selected_engagement_types = attribute_rng.choice(
            engagement_types,
            size=count,
        ).tolist()
        selected_directions = attribute_rng.choice(
            directions,
            size=count,
        ).tolist()
        timestamp_draws = date_rng.random(count)
        created_lag = rules["created_lag_hours"]
        created_hour_offsets = date_rng.integers(
            int(created_lag["minimum"]),
            int(created_lag["maximum"]) + 1,
            size=count,
        )
        rows: list[dict[str, Any]] = []
        for index, interaction_id in enumerate(self.generator.make_integer_ids(count)):
            contact_id = int(selected_contacts[index])
            is_organic = interaction_id == 1 or organic_draws[index] < float(
                rules["organic_probability"]
            )
            campaign_id: int | str
            if is_organic:
                # Organic engagement supports the campaign LEFT JOIN unmatched case.
                campaign_id = ""
                interaction_at = _datetime_from_fraction(
                    _configured_datetime(date_windows["organic_interaction_start"]),
                    reference,
                    timestamp_draws[index],
                )
            else:
                contact_campaigns = memberships[contact_id]
                campaign_position = min(
                    int(campaign_draws[index] * len(contact_campaigns)),
                    len(contact_campaigns) - 1,
                )
                campaign_id = contact_campaigns[campaign_position]
                start, end = campaign_windows[campaign_id]
                interaction_at = _datetime_from_fraction(
                    start,
                    min(end, reference),
                    timestamp_draws[index],
                )
            contact_account = contact_accounts[contact_id]
            account_id = (
                ""
                if contact_account == ""
                or account_null_draws[index] < float(rules["account_null_probability"])
                else int(contact_account)
            )
            engagement_type = str(selected_engagement_types[index])
            # The record may lag the event, but cannot be created in the future.
            created = min(
                interaction_at + timedelta(hours=int(created_hour_offsets[index])),
                reference,
            )
            rows.append(
                {
                    "interaction_id": interaction_id,
                    "contact_id": contact_id,
                    "account_id": account_id,
                    "campaign_id": campaign_id,
                    "engagement_type": engagement_type,
                    "channel": mappings["engagement_channels"][engagement_type],
                    "direction": str(selected_directions[index]),
                    "engagement_points": mappings["engagement_points"][engagement_type],
                    "interaction_at": _format_timestamp(interaction_at),
                    "created_at": _format_timestamp(created),
                }
            )
        return pd.DataFrame(rows, columns=CRM_COLUMN_CONTRACTS["interactions"])

    def generate_support_cases(self, accounts: Any, contacts: Any) -> Any:
        """Generate the base CRM support-cases fact table.

        Each row represents one customer support case associated with a valid
        account and, optionally, a contact belonging to that account. Every
        account receives coverage, priority determines the SLA deadline,
        resolved statuses receive coherent resolution timestamps, and unresolved
        statuses remain open for backlog and SLA analysis.

        Args:
            accounts: Generated accounts to which cases must belong.
            contacts: Generated contacts eligible for optional case assignment.

        Returns:
            A DataFrame matching the configured ``support_cases`` column contract.
        """

        pd = _require_pandas()
        count = self.generator.row_count("support_cases")
        relationship_rng = self.generator.rng_for("crm:support_cases:relationships")
        attribute_rng = self.generator.rng_for("crm:support_cases:attributes")
        date_rng = self.generator.rng_for("crm:support_cases:dates")
        rules = self.generation_rules["support_cases"]
        date_windows = self.generation_rules["date_windows"]
        account_ids = [int(value) for value in accounts["account_id"].tolist()]
        contact_limit = max(
            1,
            int(len(contacts) * float(rules["eligible_contact_fraction"])),
        )
        contacts_by_account: dict[int, list[int]] = {}
        # Restrict eligible contacts so some contacts remain without support cases.
        for row in contacts.iloc[:contact_limit][
            ["contact_id", "account_id"]
        ].itertuples(index=False):
            if row.account_id != "":
                contacts_by_account.setdefault(int(row.account_id), []).append(
                    int(row.contact_id)
                )

        values = self.crm_config["domain_values"]
        mappings = self.crm_config["business_mappings"]
        statuses = attribute_rng.choice(
            values["support_case_statuses"],
            size=count,
            p=_ordered_weights(
                values["support_case_statuses"],
                rules["status_weights"],
            ),
        ).tolist()
        priorities = attribute_rng.choice(
            values["support_case_priorities"],
            size=count,
            p=_ordered_weights(
                values["support_case_priorities"],
                rules["priority_weights"],
            ),
        ).tolist()
        categories = attribute_rng.choice(
            values["support_case_categories"], size=count
        ).tolist()
        _ensure_required_coverage(statuses, rules["required_status_coverage"])

        remaining_account_count = max(0, count - len(account_ids))
        # Cover every account once before assigning additional cases randomly.
        selected_accounts = (
            account_ids[:count]
            + relationship_rng.choice(
                account_ids,
                size=remaining_account_count,
            )
            .astype(int)
            .tolist()
        )
        contact_draws = relationship_rng.random(count)
        contact_assignment_draws = relationship_rng.random(count)
        opened_draws = date_rng.random(count)
        resolution_draws = date_rng.random(count)
        created_lag = rules["created_lag_hours"]
        created_hour_offsets = date_rng.integers(
            int(created_lag["minimum"]),
            int(created_lag["maximum"]) + 1,
            size=count,
        )

        reference = _reference_datetime(self.settings.reference_today)
        resolved_statuses = set(mappings["resolved_case_statuses"])
        subject_by_category = mappings["support_case_subject_by_category"]
        rows: list[dict[str, Any]] = []
        for index, case_id in enumerate(self.generator.make_integer_ids(count)):
            account_id = selected_accounts[index]
            account_contacts = contacts_by_account.get(account_id, [])
            contact_id: int | str = ""
            if account_contacts and contact_assignment_draws[index] < float(
                rules["contact_assignment_probability"]
            ):
                contact_position = min(
                    int(contact_draws[index] * len(account_contacts)),
                    len(account_contacts) - 1,
                )
                contact_id = account_contacts[contact_position]

            status = str(statuses[index])
            priority = str(priorities[index])
            # Resolved cases need enough historical room for a resolution timestamp.
            if status in resolved_statuses:
                opened_end = reference - timedelta(
                    days=int(rules["resolved_opened_cutoff_days"])
                )
            else:
                opened_end = reference
            opened = _datetime_from_fraction(
                _configured_datetime(date_windows["support_case_opened_start"]),
                opened_end,
                opened_draws[index],
            )
            sla_hours = int(mappings["sla_calendar_hours"][priority])
            sla_due = opened + timedelta(hours=sla_hours)
            if status in resolved_statuses:
                minimum_resolution_hours = int(rules["minimum_resolution_hours"])
                resolution_span = max(
                    1,
                    int(sla_hours * float(rules["resolution_sla_multiplier"]))
                    - minimum_resolution_hours
                    + 1,
                )
                resolution_hours = minimum_resolution_hours + int(
                    resolution_draws[index] * resolution_span
                )
                resolved_at = _format_timestamp(
                    opened + timedelta(hours=resolution_hours)
                )
            else:
                resolved_at = ""
            created = min(
                opened + timedelta(hours=int(created_hour_offsets[index])),
                reference,
            )
            category = str(categories[index])
            rows.append(
                {
                    "case_id": case_id,
                    "account_id": account_id,
                    "contact_id": contact_id,
                    "case_number": f"CASE-{case_id:08d}",
                    "subject": f"{subject_by_category[category]} {case_id:08d}",
                    "category": category,
                    "priority": priority,
                    "status": status,
                    "opened_at": _format_timestamp(opened),
                    "sla_due_at": _format_timestamp(sla_due),
                    "resolved_at": resolved_at,
                    "created_at": _format_timestamp(created),
                }
            )
        return pd.DataFrame(rows, columns=CRM_COLUMN_CONTRACTS["support_cases"])

    def _validate_generated_tables(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Generated CRM table order differs from config")
        for table_name, columns in CRM_COLUMN_CONTRACTS.items():
            table = tables[table_name]
            if table.columns.tolist() != columns:
                raise ValueError(f"{table_name} generated columns differ from contract")
            if len(table) != self.generator.row_count(table_name):
                raise ValueError(
                    f"{table_name} generated row count differs from config"
                )
            primary_key = self.crm_config["tables"][table_name]["primary_key"]
            primary_fields = (
                [primary_key] if isinstance(primary_key, str) else primary_key
            )
            if table.duplicated(primary_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in self.crm_config["tables"][table_name]["fields"]:
                if not field["nullable"]:
                    _assert_no_blanks(
                        table[field["name"]], f"{table_name}.{field['name']}"
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

        if memberships.groupby("contact_id")["campaign_id"].nunique().max() < 2:
            raise ValueError("contact_campaigns lacks multiple campaigns per contact")
        if memberships.groupby("campaign_id")["contact_id"].nunique().max() < 2:
            raise ValueError("contact_campaigns lacks multiple contacts per campaign")
        primary_counts = (
            memberships[memberships["is_primary_attribution"]]
            .groupby("contact_id")
            .size()
        )
        attribution = self.crm_config["business_mappings"]["attribution"]
        maximum_primary = int(attribution["maximum_primary_campaigns_per_contact"])
        if not primary_counts.empty and int(primary_counts.max()) > maximum_primary:
            raise ValueError("A contact exceeds the configured primary-campaign limit")
        attribution_sums = memberships.groupby("contact_id")[
            "attribution_weight"
        ].apply(lambda values: sum(Decimal(value) for value in values))
        expected_sum = Decimal(attribution["expected_sum_before_imperfections"])
        if not all(value == expected_sum for value in attribution_sums):
            raise ValueError(
                "Contact attribution weights do not sum to the configured value"
            )

        campaign_windows = _campaign_windows(
            campaigns,
            self.settings.reference_today,
            int(self.generation_rules["campaigns"]["future_open_window_days"]),
        )
        for row in campaigns.itertuples(index=False):
            if row.end_date != "" and row.end_date < row.start_date:
                raise ValueError("Campaign end date precedes its start date")
        for row in memberships.itertuples(index=False):
            first_touch = _as_datetime(row.first_touch_at)
            last_touch = _as_datetime(row.last_touch_at)
            campaign_start, campaign_end = campaign_windows[row.campaign_id]
            if not campaign_start <= first_touch <= last_touch <= campaign_end:
                raise ValueError("Campaign membership touches are outside its window")
            weight = Decimal(row.attribution_weight)
            if (
                not Decimal(attribution["minimum_weight"])
                <= weight
                <= Decimal(attribution["maximum_weight"])
            ):
                raise ValueError("Campaign attribution weight is outside its range")

        membership_pairs = set(
            zip(memberships["contact_id"], memberships["campaign_id"], strict=True)
        )
        attributed_interactions = interactions[interactions["campaign_id"] != ""]
        if any(
            (int(row.contact_id), int(row.campaign_id)) not in membership_pairs
            for row in attributed_interactions.itertuples(index=False)
        ):
            raise ValueError("An attributed interaction has no campaign membership")
        contact_accounts = dict(
            zip(contacts["contact_id"], contacts["account_id"], strict=True)
        )
        for row in interactions.itertuples(index=False):
            if (
                row.account_id != ""
                and row.account_id != contact_accounts[row.contact_id]
            ):
                raise ValueError("Interaction account does not match its contact")
            mappings = self.crm_config["business_mappings"]
            if row.channel != mappings["engagement_channels"][row.engagement_type]:
                raise ValueError("Interaction type and channel disagree")
            if (
                row.engagement_points
                != mappings["engagement_points"][row.engagement_type]
            ):
                raise ValueError("Interaction type and engagement points disagree")
            interaction_at = _as_datetime(row.interaction_at)
            if _as_datetime(row.created_at) < interaction_at:
                raise ValueError("Interaction creation precedes the event")
            if row.campaign_id != "":
                campaign_start, campaign_end = campaign_windows[row.campaign_id]
                if not campaign_start <= interaction_at <= campaign_end:
                    raise ValueError(
                        "Attributed interaction is outside campaign window"
                    )
        for row in support_cases.itertuples(index=False):
            if (
                row.contact_id != ""
                and contact_accounts[row.contact_id] != row.account_id
            ):
                raise ValueError("Support-case contact does not match its account")
            resolved = row.status in set(
                self.crm_config["business_mappings"]["resolved_case_statuses"]
            )
            if resolved != (row.resolved_at != ""):
                raise ValueError("Support-case status and resolved_at disagree")
            if _as_datetime(row.sla_due_at) < _as_datetime(row.opened_at):
                raise ValueError("Support-case SLA deadline precedes opening")
            expected_sla = _as_datetime(row.opened_at) + timedelta(
                hours=int(
                    self.crm_config["business_mappings"]["sla_calendar_hours"][
                        row.priority
                    ]
                )
            )
            if _as_datetime(row.sla_due_at) != expected_sla:
                raise ValueError("Support-case SLA deadline disagrees with priority")
            if row.resolved_at != "" and _as_datetime(row.resolved_at) < _as_datetime(
                row.opened_at
            ):
                raise ValueError("Support-case resolution precedes opening")
        currency_code = self.crm_config["business_mappings"]["currency_code"]
        if set(campaigns["currency_code"]) != {currency_code}:
            raise ValueError("CRM campaigns must be USD-only")


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for CRM generation") from exc
    return pd


def _reference_datetime(reference_today: date) -> datetime:
    return datetime.combine(reference_today, time(23, 59, 59))


def _configured_datetime(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.min)


def _ordered_weights(values: list[str], weights: dict[str, Any]) -> list[float]:
    return [float(weights[value]) for value in values]


def _ensure_required_coverage(values: list[Any], required_values: list[Any]) -> None:
    for position, value in enumerate(required_values[: len(values)]):
        values[position] = value


def _random_date(rng: Any, start: date, end: date) -> date:
    if end < start:
        raise ValueError("Random date range end cannot precede start")
    day_count = (end - start).days
    return start + timedelta(days=int(rng.integers(0, day_count + 1)))


def _random_datetime(rng: Any, start: datetime, end: datetime) -> datetime:
    if end < start:
        raise ValueError("Random timestamp range end cannot precede start")
    second_count = int((end - start).total_seconds())
    return start + timedelta(seconds=int(rng.integers(0, second_count + 1)))


def _datetime_from_fraction(
    start: datetime,
    end: datetime,
    fraction: float,
) -> datetime:
    """Map a seeded value in [0, 1) onto an inclusive timestamp range."""

    if end < start:
        raise ValueError("Random timestamp range end cannot precede start")
    second_count = int((end - start).total_seconds())
    offset = min(int(fraction * (second_count + 1)), second_count)
    return start + timedelta(seconds=offset)


def _random_timestamp_string(rng: Any, start: datetime, end: datetime) -> str:
    return _format_timestamp(_random_datetime(rng, start, end))


def _random_timestamp_strings(
    rng: Any,
    count: int,
    start: datetime,
    end: datetime,
) -> list[str]:
    return [_random_timestamp_string(rng, start, end) for _ in range(count)]


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def _normalized_company_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", value)
    normalized = " ".join(normalized.split())
    return normalized or "Synthetic Company"


def _synthetic_email(first_name: str, last_name: str, contact_id: int) -> str:
    local = re.sub(
        r"[^a-z0-9]+",
        ".",
        f"{first_name}.{last_name}".lower(),
    ).strip(".")
    return f"{local}.{contact_id:06d}@example.test"


def _single_line_address(fake: Any) -> str:
    return " ".join(str(fake.address()).replace("\n", " ").split())


def _append_pair(
    pairs: list[tuple[int, int]],
    pair_set: set[tuple[int, int]],
    pair: tuple[int, int],
) -> None:
    if pair not in pair_set:
        pair_set.add(pair)
        pairs.append(pair)


def _campaign_windows(
    campaigns: Any,
    reference_today: date,
    future_open_window_days: int,
) -> dict[int, tuple[datetime, datetime]]:
    windows: dict[int, tuple[datetime, datetime]] = {}
    for row in campaigns[["campaign_id", "start_date", "end_date"]].itertuples(
        index=False
    ):
        start_date = date.fromisoformat(str(row.start_date))
        if row.end_date == "":
            end_date = max(start_date, reference_today)
            if start_date > reference_today:
                end_date = start_date + timedelta(days=future_open_window_days)
        else:
            end_date = date.fromisoformat(str(row.end_date))
        windows[int(row.campaign_id)] = (
            datetime.combine(start_date, time.min),
            datetime.combine(end_date, time(23, 59, 59)),
        )
    return windows


def _assert_no_blanks(values: Any, field_name: str) -> None:
    if any(str(value) == "" for value in values.tolist()):
        raise ValueError(f"Required field contains blanks: {field_name}")


def _assert_fk(values: Any, parent_ids: set[Any], field_name: str) -> None:
    invalid = {
        value
        for value in values.tolist()
        if str(value) != "" and value not in parent_ids
    }
    if invalid:
        raise ValueError(f"{field_name} contains invalid references: {sorted(invalid)}")
