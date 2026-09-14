"""CRM distribution application for clean engagement entities."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.distributions import gaussian_mixture_dates
from generators.core.distributions import gaussian_mixture_timestamps
from generators.core.distributions import pareto_decimal_strings
from generators.core.distributions import poisson_weights
from generators.core.progress import ProgressReporter
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.generator import CRMBaseEntityGenerator


class CRMDistributionApplier:
    """Apply deterministic statistical distributions to clean CRM tables.

    The applier reshapes campaign budgets, interaction frequency, and temporal
    patterns while preserving table schemas, row counts, nullable positions,
    foreign-key validity, and the configured CRM business rules.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMDistributionApplier only supports the crm domain")
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
    ) -> "CRMDistributionApplier":
        """Create a CRM distribution applier from validated config files."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_distributed_tables(self) -> dict[str, Any]:
        """Generate clean CRM entities and apply all configured distributions.

        This is the complete distribution-stage entry point: it creates a fresh
        deterministic base dataset and passes copies through campaign-budget,
        interaction-frequency, and date-clustering transformations.

        Returns:
            A table-name-to-DataFrame mapping containing distributed CRM data.
        """

        base_tables = CRMBaseEntityGenerator(
            self.generator, progress=self.progress
        ).generate_tables()
        return self.apply_to_tables(base_tables)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Apply CRM distributions to an existing clean table mapping.

        Input tables are validated and deep-copied before modification. The
        returned tables retain the original schemas, row counts, relationships,
        and NULL locations while configured numeric, frequency, and date values
        receive deterministic statistical distributions.

        Args:
            tables: Valid clean CRM DataFrames keyed by configured table name.

        Returns:
            A new table mapping containing the distributed DataFrames.
        """

        self._report("Applying CRM distributions")
        self._validate_input_tables(tables)
        distributed = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }
        self._apply_campaign_budget_distribution(distributed["campaigns"])
        self._report("Applied Pareto campaign-budget distribution")
        self._apply_interaction_frequency_distribution(distributed)
        self._report("Applied Poisson interaction-frequency distribution")
        self._apply_date_distributions(distributed)
        self._report("Applied Gaussian-mixture date distributions")
        self._report("Validating distributed CRM tables")
        self._validate_distributed_tables(distributed, source_tables=tables)
        self._report("CRM distribution application complete")
        return distributed

    def _report(self, message: str) -> None:
        """Report progress when the caller supplied a reporter."""

        if self.progress is not None:
            self.progress.report(message)

    def _validate_input_tables(self, tables: dict[str, Any]) -> None:
        """Reject malformed or relationally invalid pre-distribution tables."""

        CRMBaseEntityGenerator(self.generator)._validate_generated_tables(tables)

    def _apply_campaign_budget_distribution(self, campaigns: Any) -> None:
        """Replace populated campaign budgets with bounded Pareto values.

        The transformation mutates only non-empty ``budget_amount`` values,
        retains the configured decimal scale, and leaves existing NULL positions
        unchanged for later missing-budget benchmark questions.
        """

        spec = self.settings.distributions["campaign_budget"]
        populated = campaigns["budget_amount"].astype(str).ne("")
        rng = self.generator.rng_for("distribution:campaigns:budget_amount")
        campaigns.loc[populated, "budget_amount"] = pareto_decimal_strings(
            rng,
            count=int(populated.sum()),
            alpha=float(spec["alpha"]),
            min_amount=int(spec["min_amount"]),
            max_amount=int(spec["max_amount"]),
            scale=int(spec["scale"]),
        )

    def _apply_interaction_frequency_distribution(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Redistribute interaction relationships using Poisson-derived weights.

        The method mutates relationship columns in ``interactions`` so selected
        contacts and campaigns receive uneven but deterministic event frequency.
        Attributed events are restricted to valid contact/campaign memberships;
        organic events remain unattributed and account consistency is preserved.
        """

        np = _require_numpy()
        interactions = tables["interactions"]
        contacts = tables["contacts"]
        campaigns = tables["campaigns"]
        memberships = tables["contact_campaigns"]
        rng = self.generator.rng_for("distribution:interactions:frequency")
        lam = float(self.settings.distributions["interaction_frequency"]["lambda"])
        rules = self.generation_rules["interactions"]
        future_statuses = set(
            self.crm_config["business_mappings"]["campaign_status_rules"][
                "future_start"
            ]
        )

        campaign_limit = max(
            1,
            int(len(campaigns) * float(rules["attributed_campaign_fraction"])),
        )
        # Future campaigns and memberships outside the attribution pool stay unused.
        eligible_campaigns = set(
            int(row.campaign_id)
            for row in campaigns.iloc[:campaign_limit][
                ["campaign_id", "status"]
            ].itertuples(index=False)
            if row.status not in future_statuses
        )
        candidate_pairs = [
            (int(row.contact_id), int(row.campaign_id))
            for row in memberships[["contact_id", "campaign_id"]].itertuples(
                index=False
            )
            if int(row.campaign_id) in eligible_campaigns
        ]
        eligible_contacts = sorted({contact_id for contact_id, _ in candidate_pairs})
        contact_limit = max(
            1,
            int(len(eligible_contacts) * float(rules["eligible_contact_fraction"])),
        )
        eligible_contacts = eligible_contacts[:contact_limit]
        eligible_contact_set = set(eligible_contacts)
        candidate_pairs = [
            pair for pair in candidate_pairs if pair[0] in eligible_contact_set
        ]
        if not eligible_contacts or not candidate_pairs:
            raise ValueError("No contact/campaign memberships support interactions")

        contact_weight_values = poisson_weights(rng, len(eligible_contacts), lam)
        contact_weight_by_id = dict(
            zip(eligible_contacts, contact_weight_values, strict=True)
        )
        campaign_ids = sorted({campaign_id for _, campaign_id in candidate_pairs})
        campaign_weight_values = poisson_weights(rng, len(campaign_ids), lam)
        campaign_weight_by_id = dict(
            zip(campaign_ids, campaign_weight_values, strict=True)
        )
        # Combined contact and campaign weights create uneven valid pair frequencies.
        pair_weights = np.array(
            [
                contact_weight_by_id[contact_id] * campaign_weight_by_id[campaign_id]
                for contact_id, campaign_id in candidate_pairs
            ],
            dtype=float,
        )
        pair_weights /= pair_weights.sum()

        organic_positions = interactions.index[
            interactions["campaign_id"].astype(str).eq("")
        ].to_numpy()
        attributed_positions = interactions.index[
            interactions["campaign_id"].astype(str).ne("")
        ].to_numpy()
        # Preserve the configured split between organic and attributed interactions.
        contact_weights = np.array(contact_weight_values, dtype=float)
        contact_weights /= contact_weights.sum()
        organic_contacts = rng.choice(
            eligible_contacts,
            size=len(organic_positions),
            p=contact_weights,
        )
        pair_positions = rng.choice(
            len(candidate_pairs),
            size=len(attributed_positions),
            p=pair_weights,
        )
        selected_pairs = [candidate_pairs[int(position)] for position in pair_positions]

        interactions.loc[organic_positions, "contact_id"] = organic_contacts
        interactions.loc[organic_positions, "campaign_id"] = ""
        interactions.loc[attributed_positions, "contact_id"] = [
            contact_id for contact_id, _ in selected_pairs
        ]
        interactions.loc[attributed_positions, "campaign_id"] = [
            campaign_id for _, campaign_id in selected_pairs
        ]

        contact_accounts = dict(
            zip(contacts["contact_id"], contacts["account_id"], strict=True)
        )
        # Keep deliberate account NULLs while realigning populated account IDs.
        retain_null_account = interactions["account_id"].astype(str).eq("")
        interactions["account_id"] = [
            (
                ""
                if retain_null_account.iloc[position]
                or contact_accounts[int(contact_id)] == ""
                else int(contact_accounts[int(contact_id)])
            )
            for position, contact_id in enumerate(interactions["contact_id"])
        ]

    def _apply_date_distributions(self, tables: dict[str, Any]) -> None:
        """Apply Gaussian-mixture clustering across CRM temporal fields.

        Parent and anchor dates are clustered first. Membership touches,
        interaction creation times, campaign boundaries, SLA deadlines, and
        resolution times are then derived so chronological rules remain valid.
        """

        self._cluster_entity_creation_timestamps(tables)
        self._cluster_campaign_dates(tables["campaigns"])
        self._cluster_membership_timestamps(tables)
        self._cluster_interaction_timestamps(tables)
        self._cluster_support_case_timestamps(tables["support_cases"])

    def _cluster_entity_creation_timestamps(self, tables: dict[str, Any]) -> None:
        """Cluster account and contact creation times over the entity window."""

        spec = self.settings.distributions["date_clustering"]
        start = date.fromisoformat(
            self.generation_rules["date_windows"]["entity_distribution_start"]
        )
        for table_name in ("accounts", "contacts"):
            rng = self.generator.rng_for(
                f"distribution:timestamps:{table_name}:created_at"
            )
            tables[table_name]["created_at"] = _clustered_timestamps(
                rng,
                len(tables[table_name]),
                start,
                self.settings.reference_today,
                spec,
            )

    def _cluster_campaign_dates(self, campaigns: Any) -> None:
        """Cluster campaign starts and rederive status-aware lifecycle dates.

        Historical and future campaigns are sampled in separate valid windows.
        End dates and creation timestamps are rebuilt from each new start date
        so campaign status and temporal ordering remain coherent.
        """

        spec = self.settings.distributions["date_clustering"]
        reference_today = self.settings.reference_today
        rng = self.generator.rng_for("distribution:campaigns:dates")
        rules = self.generation_rules["campaigns"]
        status_rules = self.crm_config["business_mappings"]["campaign_status_rules"]
        future_statuses = set(status_rules["future_start"])
        requires_end_date = set(status_rules["requires_end_date"])
        future_offsets = rules["future_start_offset_days"]
        historical_durations = rules["historical_end_duration_days"]
        future_durations = rules["future_end_duration_days"]
        creation_lead = rules["creation_lead_days"]
        start_dates = [""] * len(campaigns)
        # Separate windows prevent future campaign statuses from receiving past starts.
        for is_future, statuses in (
            (False, set(campaigns["status"]) - future_statuses),
            (True, future_statuses),
        ):
            positions = campaigns.index[campaigns["status"].isin(statuses)].to_numpy()
            if not len(positions):
                continue
            start = (
                reference_today + timedelta(days=int(future_offsets["minimum"]))
                if is_future
                else date.fromisoformat(
                    self.generation_rules["date_windows"]["campaign_historical_start"]
                )
            )
            end = (
                reference_today + timedelta(days=int(future_offsets["maximum"]))
                if is_future
                else reference_today
                - timedelta(days=int(historical_durations["minimum"]))
            )
            values = gaussian_mixture_dates(
                rng,
                count=len(positions),
                start=start,
                end=end,
                component_centers=spec["component_centers"],
                component_weights=spec["component_weights"],
                std_fraction=float(spec["std_fraction"]),
            )
            for position, value in zip(positions, values, strict=True):
                start_dates[int(position)] = value

        historical_duration_days = rng.integers(
            int(historical_durations["minimum"]),
            int(historical_durations["maximum"]) + 1,
            size=len(campaigns),
        )
        future_duration_days = rng.integers(
            int(future_durations["minimum"]),
            int(future_durations["maximum"]) + 1,
            size=len(campaigns),
        )
        creation_lead_days = rng.integers(
            int(creation_lead["minimum"]),
            int(creation_lead["maximum"]) + 1,
            size=len(campaigns),
        )
        end_dates: list[str] = []
        created_at: list[str] = []
        for position, row in enumerate(campaigns.itertuples(index=False)):
            campaign_start = date.fromisoformat(start_dates[position])
            if row.status in requires_end_date:
                duration_days = (
                    future_duration_days[position]
                    if row.status in future_statuses
                    else historical_duration_days[position]
                )
                campaign_end = campaign_start + timedelta(days=int(duration_days))
                if row.status not in future_statuses:
                    campaign_end = min(campaign_end, reference_today)
                end_dates.append(campaign_end.isoformat())
            else:
                end_dates.append("")
            created = datetime.combine(campaign_start, time.min) - timedelta(
                days=int(creation_lead_days[position])
            )
            created_at.append(_format_timestamp(created))
        campaigns["start_date"] = start_dates
        campaigns["end_date"] = end_dates
        campaigns["created_at"] = created_at

    def _cluster_membership_timestamps(self, tables: dict[str, Any]) -> None:
        """Cluster campaign-member touches inside each campaign window.

        The method mutates ``first_touch_at``, ``last_touch_at``, and
        ``created_at`` while guaranteeing first touch is not after last touch.
        """

        memberships = tables["contact_campaigns"]
        windows = _campaign_windows(
            tables["campaigns"],
            self.settings.reference_today,
            int(self.generation_rules["campaigns"]["future_open_window_days"]),
        )
        spec = self.settings.distributions["date_clustering"]
        rng = self.generator.rng_for("distribution:contact_campaigns:touches")
        first_values = [""] * len(memberships)
        last_values = [""] * len(memberships)
        for campaign_id, positions in memberships.groupby("campaign_id").groups.items():
            start, end = windows[int(campaign_id)]
            first_samples = _clustered_timestamps(
                rng, len(positions), start.date(), end.date(), spec
            )
            last_samples = _clustered_timestamps(
                rng, len(positions), start.date(), end.date(), spec
            )
            for position, first_value, last_value in zip(
                positions,
                first_samples,
                last_samples,
                strict=True,
            ):
                # Independent samples are ordered to preserve touch chronology.
                ordered = sorted((first_value, last_value))
                first_values[int(position)] = ordered[0]
                last_values[int(position)] = ordered[1]
        memberships["first_touch_at"] = first_values
        memberships["last_touch_at"] = last_values
        memberships["created_at"] = first_values

    def _cluster_interaction_timestamps(self, tables: dict[str, Any]) -> None:
        """Cluster interaction events within organic or campaign date windows.

        Organic events use the configured general activity period; attributed
        events use their campaign window. ``created_at`` is then rederived from
        each event with the configured lag and fixed reference-date ceiling.
        """

        interactions = tables["interactions"]
        rules = self.generation_rules["interactions"]
        windows = _campaign_windows(
            tables["campaigns"],
            self.settings.reference_today,
            int(self.generation_rules["campaigns"]["future_open_window_days"]),
        )
        spec = self.settings.distributions["date_clustering"]
        rng = self.generator.rng_for("distribution:interactions:timestamps")
        interaction_values = [""] * len(interactions)
        organic_positions = interactions.index[
            interactions["campaign_id"].astype(str).eq("")
        ].to_numpy()
        organic_values = _clustered_timestamps(
            rng,
            len(organic_positions),
            date.fromisoformat(
                self.generation_rules["date_windows"]["organic_interaction_start"]
            ),
            self.settings.reference_today,
            spec,
        )
        for position, value in zip(organic_positions, organic_values, strict=True):
            interaction_values[int(position)] = value

        attributed = interactions[interactions["campaign_id"].astype(str).ne("")]
        for campaign_id, positions in attributed.groupby("campaign_id").groups.items():
            start, end = windows[int(campaign_id)]
            end = min(end, _reference_datetime(self.settings.reference_today))
            values = _clustered_timestamps(
                rng, len(positions), start.date(), end.date(), spec
            )
            for position, value in zip(positions, values, strict=True):
                interaction_values[int(position)] = value

        created_lag = rules["created_lag_hours"]
        created_hour_offsets = rng.integers(
            int(created_lag["minimum"]),
            int(created_lag["maximum"]) + 1,
            size=len(interactions),
        )
        reference = _reference_datetime(self.settings.reference_today)
        created_values = [
            _format_timestamp(
                min(
                    _parse_timestamp(value)
                    + timedelta(hours=int(created_hour_offsets[position])),
                    reference,
                )
            )
            for position, value in enumerate(interaction_values)
        ]
        interactions["interaction_at"] = interaction_values
        interactions["created_at"] = created_values

    def _cluster_support_case_timestamps(self, support_cases: Any) -> None:
        """Cluster support-case openings and rebuild dependent SLA timestamps.

        Resolved and unresolved cases use separate valid opening windows. SLA
        deadlines are recalculated from priority, resolved cases receive a new
        resolution time, and unresolved cases keep ``resolved_at`` empty.
        """

        spec = self.settings.distributions["date_clustering"]
        mappings = self.crm_config["business_mappings"]
        rules = self.generation_rules["support_cases"]
        resolved_statuses = set(mappings["resolved_case_statuses"])
        rng = self.generator.rng_for("distribution:support_cases:timestamps")
        reference_today = self.settings.reference_today
        opened_values = [""] * len(support_cases)
        # Resolved cases need enough historical room for a valid resolution.
        for is_resolved in (False, True):
            positions = support_cases.index[
                support_cases["status"].isin(resolved_statuses) == is_resolved
            ].to_numpy()
            end = (
                reference_today
                - timedelta(days=int(rules["resolved_opened_cutoff_days"]))
                if is_resolved
                else reference_today
            )
            values = _clustered_timestamps(
                rng,
                len(positions),
                date.fromisoformat(
                    self.generation_rules["date_windows"]["support_case_opened_start"]
                ),
                end,
                spec,
            )
            for position, value in zip(positions, values, strict=True):
                opened_values[int(position)] = value

        resolution_draws = rng.random(len(support_cases))
        created_lag = rules["created_lag_hours"]
        created_offsets = rng.integers(
            int(created_lag["minimum"]),
            int(created_lag["maximum"]) + 1,
            size=len(support_cases),
        )
        reference = _reference_datetime(reference_today)
        sla_values: list[str] = []
        resolved_values: list[str] = []
        created_values: list[str] = []
        for position, row in enumerate(support_cases.itertuples(index=False)):
            opened = _parse_timestamp(opened_values[position])
            sla_hours = int(mappings["sla_calendar_hours"][row.priority])
            sla_values.append(_format_timestamp(opened + timedelta(hours=sla_hours)))
            if row.status in resolved_statuses:
                minimum_resolution_hours = int(rules["minimum_resolution_hours"])
                resolution_span = max(
                    1,
                    int(sla_hours * float(rules["resolution_sla_multiplier"]))
                    - minimum_resolution_hours
                    + 1,
                )
                resolution_hours = minimum_resolution_hours + int(
                    resolution_draws[position] * resolution_span
                )
                resolved_values.append(
                    _format_timestamp(opened + timedelta(hours=resolution_hours))
                )
            else:
                resolved_values.append("")
            created_values.append(
                _format_timestamp(
                    min(
                        opened + timedelta(hours=int(created_offsets[position])),
                        reference,
                    )
                )
            )
        support_cases["opened_at"] = opened_values
        support_cases["sla_due_at"] = sla_values
        support_cases["resolved_at"] = resolved_values
        support_cases["created_at"] = created_values

    def _validate_distributed_tables(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Ensure distribution output preserves contracts and budget constraints.

        The guard validates CRM relationships, table shapes, campaign-budget
        NULL positions, decimal scale, and configured Pareto bounds. It raises
        ``ValueError`` immediately when a distribution violates these contracts.
        """

        CRMBaseEntityGenerator(self.generator)._validate_generated_tables(tables)
        for table_name in self.settings.table_order:
            if (
                tables[table_name].columns.tolist()
                != source_tables[table_name].columns.tolist()
            ):
                raise ValueError(f"{table_name} columns changed after distributions")
            if len(tables[table_name]) != len(source_tables[table_name]):
                raise ValueError(f"{table_name} row count changed after distributions")

        source_budget_nulls = int(
            source_tables["campaigns"]["budget_amount"].astype(str).eq("").sum()
        )
        distributed_budgets = tables["campaigns"]["budget_amount"].astype(str)
        if int(distributed_budgets.eq("").sum()) != source_budget_nulls:
            raise ValueError("Campaign budget distribution changed NULL placement")
        populated_budgets = distributed_budgets[distributed_budgets.ne("")]
        spec = self.settings.distributions["campaign_budget"]
        if not populated_budgets.map(
            lambda value: Decimal(value).as_tuple().exponent == -int(spec["scale"])
        ).all():
            raise ValueError("Campaign budgets do not retain fixed decimal scale")
        numeric_budgets = populated_budgets.astype(float)
        if not numeric_budgets.between(
            float(spec["min_amount"]), float(spec["max_amount"])
        ).all():
            raise ValueError("Campaign budgets are outside configured bounds")


def _clustered_timestamps(
    rng: Any,
    count: int,
    start: date,
    end: date,
    spec: dict[str, Any],
) -> list[str]:
    """Return formatted Gaussian-mixture timestamps for a bounded date range."""

    return gaussian_mixture_timestamps(
        rng,
        count=count,
        start=start,
        end=end,
        component_centers=spec["component_centers"],
        component_weights=spec["component_weights"],
        std_fraction=float(spec["std_fraction"]),
    )


def _campaign_windows(
    campaigns: Any,
    reference_today: date,
    future_open_window_days: int,
) -> dict[int, tuple[datetime, datetime]]:
    """Return inclusive activity windows keyed by campaign ID.

    Explicit campaign end dates are honored. Open historical campaigns extend
    through the fixed reference date, while open future campaigns receive the
    configured forward-looking window.
    """

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


def _reference_datetime(reference_today: date) -> datetime:
    """Convert the fixed reference date to its final whole-second timestamp."""

    return datetime.combine(reference_today, time(23, 59, 59))


def _parse_timestamp(value: Any) -> datetime:
    """Parse a configured ISO timestamp value into a datetime."""

    return datetime.fromisoformat(str(value))


def _format_timestamp(value: datetime) -> str:
    """Format a datetime as an ISO timestamp without fractional seconds."""

    return value.replace(microsecond=0).isoformat()


def _require_numpy() -> Any:
    """Import NumPy lazily and provide a domain-specific dependency error."""

    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("numpy is required for CRM distributions") from exc
    return np
