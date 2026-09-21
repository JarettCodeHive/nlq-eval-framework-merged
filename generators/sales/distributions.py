"""Deterministic distribution application for clean Sales entities."""

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
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.distributed_tables import (
    validate_sales_distributed_tables,
)
from generators.sales.validators.generated_tables import (
    validate_sales_generated_tables,
)


class SalesDistributionApplier:
    """Apply realistic distributions without changing the Sales contract.

    A fresh base dataset can be generated through
    :meth:`generate_distributed_tables`, or an existing in-memory base mapping
    can be supplied to :meth:`apply_to_tables`. Deal and quota amounts receive
    bounded Pareto values, quotation lines are redistributed between deals
    using Poisson-derived frequencies, and configured dates are clustered with
    a Gaussian mixture. Dependent values are rebuilt so ownership, foreign
    keys, quote-line uniqueness, row counts, and chronology remain valid.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesDistributionApplier only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()
        self.generation_rules = self.sales_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "SalesDistributionApplier":
        """Create an applier from the validated Sales profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_distributed_tables(self) -> dict[str, Any]:
        """Regenerate clean Sales tables in memory and apply distributions."""

        base_tables = SalesBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        return self.apply_to_tables(base_tables)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return distributed deep copies of valid clean Sales tables."""

        self._report("Applying Sales distributions")
        validate_sales_generated_tables(self.generator, tables)
        distributed = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        self._apply_amount_distribution(
            distributed["deals"],
            column_name="deal_amount",
            settings_key="deal_amount",
        )
        self._report("Applied Pareto deal-amount distribution")
        self._apply_quotation_frequency_distribution(distributed)
        self._report("Applied Poisson quotation-frequency distribution")
        self._apply_date_distributions(distributed)
        self._report("Applied Gaussian-mixture date distributions")
        self._apply_amount_distribution(
            distributed["targets"],
            column_name="quota_amount",
            settings_key="quota_amount",
        )
        self._report("Applied Pareto quota-amount distribution")

        self._report("Validating distributed Sales tables")
        validate_sales_distributed_tables(
            self.generator,
            distributed,
            source_tables=tables,
        )
        self._report("Sales distribution application complete")
        return distributed

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)

    def _apply_amount_distribution(
        self,
        table: Any,
        column_name: str,
        settings_key: str,
    ) -> None:
        """Replace one complete amount column with fixed-scale Pareto values."""

        spec = self.settings.distributions[settings_key]
        rng = self.generator.rng_for(f"distribution:sales:{settings_key}:{column_name}")
        table[column_name] = pareto_decimal_strings(
            rng,
            count=len(table),
            alpha=float(spec["alpha"]),
            min_amount=int(spec["min_amount"]),
            max_amount=int(spec["max_amount"]),
            scale=int(spec["scale"]),
        )

    def _apply_quotation_frequency_distribution(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Redistribute unique deal/product lines with Poisson deal frequency.

        Every deal keeps at least one line, every product remains represented,
        and the exact configured quotation count is preserved. Product prices
        and quote numbers are then rebuilt for the newly assigned pairs.
        """

        quotations = tables["quotations"]
        deal_ids = [int(value) for value in tables["deals"]["deal_id"]]
        product_ids = [int(value) for value in tables["products"]["product_id"]]
        rng = self.generator.rng_for("distribution:sales:quotations:frequency")
        lam = float(self.settings.distributions["quotation_frequency"]["lambda"])
        counts = _exact_poisson_counts(
            rng,
            item_count=len(deal_ids),
            total_count=len(quotations),
            lam=lam,
            maximum_per_item=len(product_ids),
        )
        pairs = _frequency_pairs(rng, deal_ids, product_ids, counts)

        rules = self.generation_rules["quotations"]
        attribute_rng = self.generator.rng_for(
            "distribution:sales:quotations:pair_attributes"
        )
        product_prices = {
            int(product_id): Decimal(price)
            for product_id, price in zip(
                tables["products"]["product_id"],
                tables["products"]["list_price"],
                strict=True,
            )
        }
        adjustment = rules["unit_price_adjustment_pct"]
        adjustment_values = attribute_rng.integers(
            int(adjustment["minimum"]),
            int(adjustment["maximum"]) + 1,
            size=len(pairs),
        )

        quotations["deal_id"] = [deal_id for deal_id, _ in pairs]
        quotations["product_id"] = [product_id for _, product_id in pairs]
        quotations["quote_number"] = _quote_numbers(
            attribute_rng,
            pairs,
            rules["quote_number_group_size"],
        )
        quotations["unit_price"] = [
            _decimal_string(
                product_prices[product_id]
                * (Decimal(100 + int(adjustment_pct)) / Decimal(100)),
                2,
            )
            for (_, product_id), adjustment_pct in zip(
                pairs,
                adjustment_values,
                strict=True,
            )
        ]

    def _apply_date_distributions(self, tables: dict[str, Any]) -> None:
        """Cluster configured dates and rebuild all dependent chronology."""

        spec = self.settings.distributions["date_clustering"]
        windows = self.generation_rules["date_windows"]
        entity_start = date.fromisoformat(windows["entity_created_start"])
        reference = self.settings.reference_today

        for table_name in ("leads", "products"):
            rng = self.generator.rng_for(f"distribution:sales:{table_name}:created_at")
            tables[table_name]["created_at"] = _clustered_timestamps(
                rng,
                len(tables[table_name]),
                entity_start,
                reference,
                spec,
            )

        self._cluster_deal_dates(tables)
        self._cluster_quotation_dates(tables)

    def _cluster_deal_dates(self, tables: dict[str, Any]) -> None:
        """Cluster deal dates while retaining lead and stage semantics."""

        deals = tables["deals"]
        leads = tables["leads"]
        rules = self.generation_rules["deals"]
        spec = self.settings.distributions["date_clustering"]
        reference = self.settings.reference_today
        activity_start = date.fromisoformat(
            self.generation_rules["date_windows"]["deal_activity_start"]
        )
        minimum_lag = int(rules["closed_date_lag_days"]["minimum"])
        maximum_lag = int(rules["closed_date_lag_days"]["maximum"])
        maximum_expected = int(rules["expected_close_offset_days"]["maximum"])
        rng = self.generator.rng_for("distribution:sales:deals:dates")

        created_samples = _clustered_timestamps(
            rng,
            len(deals),
            activity_start,
            reference - timedelta(days=minimum_lag),
            spec,
        )
        close_samples = _clustered_dates(
            rng,
            len(deals),
            activity_start,
            reference,
            spec,
        )
        expected_samples = _clustered_dates(
            rng,
            len(deals),
            activity_start,
            reference + timedelta(days=maximum_expected),
            spec,
        )
        lead_created = dict(zip(leads["lead_id"], leads["created_at"], strict=True))
        closed_stages = set(rules["closed_stages"])

        created_values: list[str] = []
        close_values: list[str] = []
        expected_values: list[str] = []
        for position, row in enumerate(deals.itertuples(index=False)):
            lower = max(
                datetime.combine(activity_start, time.min),
                datetime.fromisoformat(lead_created[row.lead_id]),
            )
            latest = _reference_datetime(reference) - timedelta(
                days=minimum_lag if row.stage in closed_stages else 0
            )
            created = _clamp_datetime(
                datetime.fromisoformat(created_samples[position]),
                lower,
                latest,
            )
            expected = max(
                date.fromisoformat(expected_samples[position]),
                created.date(),
            )
            if row.stage in closed_stages:
                earliest_close = created.date() + timedelta(days=minimum_lag)
                latest_close = min(
                    reference,
                    created.date() + timedelta(days=maximum_lag),
                )
                closed = _clamp_date(
                    date.fromisoformat(close_samples[position]),
                    earliest_close,
                    latest_close,
                )
                close_values.append(closed.isoformat())
            else:
                close_values.append("")
            created_values.append(_format_timestamp(created))
            expected_values.append(expected.isoformat())

        deals["created_at"] = created_values
        deals["close_date"] = close_values
        deals["expected_close_date"] = expected_values
        self._anchor_quota_attainment(tables)

    def _anchor_quota_attainment(self, tables: dict[str, Any]) -> None:
        """Keep one won deal inside a target period after date clustering."""

        deals = tables["deals"]
        leads = tables["leads"]
        targets = tables["targets"]
        won_stage = self.sales_config["business_mappings"]["quota_attainment"][
            "attained_stage"
        ]
        deal_position = int(deals.index[deals["stage"] == won_stage][0])
        deal = deals.loc[deal_position]
        target = targets[targets["rep_name"] == deal["rep_name"]].iloc[0]
        period_start = date.fromisoformat(target["period_start"])
        period_end = date.fromisoformat(target["period_end"])
        close_date = min(
            period_start + timedelta(days=45), period_end - timedelta(days=1)
        )
        created = datetime.combine(period_start - timedelta(days=31), time.min)
        lead_position = int(leads.index[leads["lead_id"] == deal["lead_id"]][0])
        if datetime.fromisoformat(leads.at[lead_position, "created_at"]) > created:
            leads.at[lead_position, "created_at"] = _format_timestamp(
                created - timedelta(days=1)
            )
        deals.at[deal_position, "created_at"] = _format_timestamp(created)
        deals.at[deal_position, "close_date"] = close_date.isoformat()
        deals.at[deal_position, "expected_close_date"] = min(
            close_date + timedelta(days=15),
            period_end - timedelta(days=1),
        ).isoformat()

    def _cluster_quotation_dates(self, tables: dict[str, Any]) -> None:
        """Cluster quote timestamps and constrain them to each deal window."""

        quotations = tables["quotations"]
        deals = tables["deals"]
        spec = self.settings.distributions["date_clustering"]
        start = date.fromisoformat(
            self.generation_rules["date_windows"]["quotation_activity_start"]
        )
        reference = self.settings.reference_today
        rng = self.generator.rng_for("distribution:sales:quotations:dates")
        created_samples = _clustered_timestamps(
            rng, len(quotations), start, reference, spec
        )
        quoted_samples = _clustered_timestamps(
            rng, len(quotations), start, reference, spec
        )
        deal_windows = {
            int(row.deal_id): (
                datetime.fromisoformat(row.created_at),
                (
                    datetime.combine(date.fromisoformat(row.close_date), time.max)
                    if row.close_date
                    else _reference_datetime(reference)
                ),
            )
            for row in deals.itertuples(index=False)
        }

        created_values: list[str] = []
        quoted_values: list[str] = []
        for position, deal_id in enumerate(quotations["deal_id"]):
            lower, upper = deal_windows[int(deal_id)]
            created = _clamp_datetime(
                datetime.fromisoformat(created_samples[position]), lower, upper
            )
            quoted = _clamp_datetime(
                datetime.fromisoformat(quoted_samples[position]), created, upper
            )
            created_values.append(_format_timestamp(created))
            quoted_values.append(_format_timestamp(quoted))
        quotations["created_at"] = created_values
        quotations["quoted_at"] = quoted_values


def _exact_poisson_counts(
    rng: Any,
    item_count: int,
    total_count: int,
    lam: float,
    maximum_per_item: int,
) -> list[int]:
    """Allocate an exact total using Poisson weights and bounded item counts."""

    if total_count < item_count:
        raise ValueError("Quotation target must provide at least one row per deal")
    if total_count > item_count * maximum_per_item:
        raise ValueError("Quotation target exceeds unique deal/product capacity")
    weights = poisson_weights(rng, item_count, lam)
    counts = 1 + rng.multinomial(total_count - item_count, weights)

    excess = 0
    for position in range(item_count):
        if int(counts[position]) > maximum_per_item:
            excess += int(counts[position]) - maximum_per_item
            counts[position] = maximum_per_item
    while excess:
        candidates = [
            position
            for position in rng.permutation(item_count)
            if int(counts[position]) < maximum_per_item
        ]
        if not candidates:
            raise ValueError("Unable to reconcile quotation frequencies")
        for position in candidates:
            counts[int(position)] += 1
            excess -= 1
            if excess == 0:
                break
    return [int(value) for value in counts]


def _frequency_pairs(
    rng: Any,
    deal_ids: list[int],
    product_ids: list[int],
    counts: list[int],
) -> list[tuple[int, int]]:
    """Create unique pairs whose per-deal counts match the allocation."""

    product_order = [int(value) for value in rng.permutation(product_ids)]
    pairs: list[tuple[int, int]] = []
    cursor = 0
    for deal_id, count in zip(deal_ids, counts, strict=True):
        selected = [
            product_order[(cursor + offset) % len(product_order)]
            for offset in range(count)
        ]
        pairs.extend((deal_id, product_id) for product_id in selected)
        cursor = (cursor + count) % len(product_order)
    return pairs


def _clustered_timestamps(
    rng: Any,
    count: int,
    start: date,
    end: date,
    spec: dict[str, Any],
) -> list[str]:
    return gaussian_mixture_timestamps(
        rng,
        count=count,
        start=start,
        end=end,
        component_centers=spec["component_centers"],
        component_weights=spec["component_weights"],
        std_fraction=float(spec["std_fraction"]),
    )


def _clustered_dates(
    rng: Any,
    count: int,
    start: date,
    end: date,
    spec: dict[str, Any],
) -> list[str]:
    return gaussian_mixture_dates(
        rng,
        count=count,
        start=start,
        end=end,
        component_centers=spec["component_centers"],
        component_weights=spec["component_weights"],
        std_fraction=float(spec["std_fraction"]),
    )


def _quote_numbers(
    rng: Any,
    pairs: list[tuple[int, int]],
    group_size: dict[str, Any],
) -> list[str]:
    counters: dict[int, int] = {}
    remaining: dict[int, int] = {}
    numbers: list[str] = []
    for deal_id, _ in pairs:
        if remaining.get(deal_id, 0) == 0:
            counters[deal_id] = counters.get(deal_id, 0) + 1
            remaining[deal_id] = int(
                rng.integers(
                    int(group_size["minimum"]),
                    int(group_size["maximum"]) + 1,
                )
            )
        numbers.append(f"SYN-Q-{deal_id:06d}-{counters[deal_id]:03d}")
        remaining[deal_id] -= 1
    return numbers


def _decimal_string(value: Decimal, scale: int) -> str:
    quantizer = Decimal(1).scaleb(-scale)
    return f"{value.quantize(quantizer):.{scale}f}"


def _reference_datetime(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59))


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _clamp_datetime(value: datetime, lower: datetime, upper: datetime) -> datetime:
    if upper < lower:
        raise ValueError("Timestamp bounds are reversed")
    return min(max(value, lower), upper)


def _clamp_date(value: date, lower: date, upper: date) -> date:
    if upper < lower:
        raise ValueError("Date bounds are reversed")
    return min(max(value, lower), upper)


__all__ = ["SalesDistributionApplier"]
