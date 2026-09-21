"""Deterministic base generation for the Sales golden dataset."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
import re
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import validate_column_contracts
from generators.core.distributions import format_fixed_decimal
from generators.core.progress import ProgressReporter
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.generated_tables import (
    validate_sales_generated_tables,
)


def build_sales_column_contracts(
    sales_config: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Build ordered table/column contracts from assembled Sales config.

    The Sales schema configuration is the sole source of dataframe column
    order. This avoids maintaining parallel column lists in generator code and
    keeps later base, distribution, imperfection, and CSV stages on the same
    contract.
    """

    config = sales_config if sales_config is not None else load_sales_config()
    contracts = {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    }
    validate_column_contracts(
        config["table_order"],
        config["tables"],
        contracts,
    )
    return contracts


SALES_COLUMN_CONTRACTS = build_sales_column_contracts()


class SalesBaseEntityGenerator:
    """Generate clean, deterministic Sales entities in dependency order.

    The generator builds leads, deals, products, quotation lines, and targets
    entirely in memory. It uses config-owned values and stable named random
    streams, preserves every physical foreign key, creates the Deal-to-Product
    many-to-many bridge through quotations, and aligns representative ownership
    across leads, deals, and target periods.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesBaseEntityGenerator only supports the sales domain")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()
        self.generation_rules = self.sales_config["generation_rules"]
        self.progress = progress
        self._representatives: list[tuple[str, str]] | None = None
        self._selected_lead_ids: list[int] | None = None

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "SalesBaseEntityGenerator":
        """Create a validated Sales base generator for one profile."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_tables(self) -> dict[str, Any]:
        """Generate all clean Sales tables in configured dependency order."""

        leads = self._generate_and_report("leads", self.generate_leads)
        deals = self._generate_and_report("deals", lambda: self.generate_deals(leads))
        products = self._generate_and_report("products", self.generate_products)
        quotations = self._generate_and_report(
            "quotations",
            lambda: self.generate_quotations(deals, products),
        )
        targets = self._generate_and_report(
            "targets",
            lambda: self.generate_targets(deals),
        )
        tables = {
            "leads": leads,
            "deals": deals,
            "products": products,
            "quotations": quotations,
            "targets": targets,
        }
        self._report("Validating base Sales tables")
        validate_sales_generated_tables(self.generator, tables)
        self._report("Base Sales generation complete")
        return tables

    def _generate_and_report(self, table_name: str, generate: Any) -> Any:
        """Generate one table and report its completed shape when enabled."""

        self._report(f"Generating {table_name}")
        table = generate()
        if self.progress is not None:
            self.progress.report_table(table_name, table)
        return table

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)

    def generate_leads(self) -> Any:
        """Generate synthetic prospects with stable ownership and conversion state.

        Lead names and companies come from seeded Faker streams. Sources,
        statuses, territories, scores, and representative ownership come from
        Sales config. Every lead selected to own a deal is marked Converted,
        while enough leads remain without deals for later LEFT JOIN questions.
        """

        pd = _require_pandas()
        count = self.generator.row_count("leads")
        rng = self.generator.rng_for("sales:leads:attributes")
        date_rng = self.generator.rng_for("sales:leads:created_at")
        fake = self.generator.faker_for("sales:leads:faker")
        values = self.sales_config["domain_values"]
        rules = self.generation_rules["leads"]
        lead_ids = self.generator.make_integer_ids(count)
        selected_for_deals = set(self._deal_lead_ids(lead_ids))
        representatives = self._representative_pool()

        statuses = rng.choice(
            values["lead_statuses"],
            size=count,
            p=_ordered_weights(values["lead_statuses"], rules["status_weights"]),
        ).tolist()
        _ensure_required_coverage(statuses, rules["required_status_coverage"])
        converted = self.sales_config["business_mappings"]["lead_conversion"][
            "deal_bearing_status"
        ]
        for index, lead_id in enumerate(lead_ids):
            if lead_id in selected_for_deals:
                statuses[index] = converted

        representative_positions = rng.integers(0, len(representatives), size=count)
        for position in range(min(count, len(representatives))):
            representative_positions[position] = position
        first_deal_position = lead_ids.index(self._deal_lead_ids(lead_ids)[0])
        displaced_representative = int(representative_positions[first_deal_position])
        representative_zero_position = int(
            next(
                position
                for position, value in enumerate(representative_positions)
                if int(value) == 0
            )
        )
        representative_positions[representative_zero_position] = (
            displaced_representative
        )
        representative_positions[first_deal_position] = 0
        created_at = _random_timestamp_strings(
            date_rng,
            count,
            _configured_datetime(
                self.generation_rules["date_windows"]["entity_created_start"]
            ),
            _reference_datetime(self.settings.reference_today),
        )
        # Anchor the seeded won-deal lead before its deliberately covered period.
        created_at[first_deal_position] = _format_timestamp(
            _configured_datetime(
                self.generation_rules["date_windows"]["deal_activity_start"]
            )
        )
        latest_closed_source = _reference_datetime(
            self.settings.reference_today
        ) - timedelta(
            days=int(self.generation_rules["deals"]["closed_date_lag_days"]["minimum"])
        )
        for position, lead_id in enumerate(lead_ids):
            if (
                lead_id in selected_for_deals
                and datetime.fromisoformat(created_at[position]) > latest_closed_source
            ):
                created_at[position] = _format_timestamp(latest_closed_source)
        lead_sources = rng.choice(values["lead_sources"], size=count).tolist()
        _ensure_required_coverage(lead_sources, values["lead_sources"])
        score = rules["score"]
        return pd.DataFrame(
            {
                "lead_id": lead_ids,
                "lead_name": [fake.name() for _ in range(count)],
                "company_name": [
                    _normalized_company_name(fake.company()) for _ in range(count)
                ],
                "lead_source": lead_sources,
                "lead_status": statuses,
                "rep_name": [
                    representatives[int(position)][0]
                    for position in representative_positions
                ],
                "territory": [
                    representatives[int(position)][1]
                    for position in representative_positions
                ],
                "score": rng.integers(
                    int(score["minimum"]),
                    int(score["maximum"]) + 1,
                    size=count,
                ).tolist(),
                "created_at": created_at,
            },
            columns=SALES_COLUMN_CONTRACTS["leads"],
        )

    def generate_deals(self, leads: Any) -> Any:
        """Generate pipeline deals tied to converted leads and their owners.

        Every deal references one valid lead and copies that lead's
        representative. Closed stages receive actual close dates; open stages
        keep them blank. One won deal is deterministically placed inside the
        first target period, while the final representative is kept free of won
        deals to support both matched and zero-attainment quota questions.
        """

        pd = _require_pandas()
        count = self.generator.row_count("deals")
        rng = self.generator.rng_for("sales:deals:attributes")
        date_rng = self.generator.rng_for("sales:deals:dates")
        values = self.sales_config["domain_values"]
        rules = self.generation_rules["deals"]
        mappings = self.sales_config["business_mappings"]
        selected_ids = self._deal_lead_ids(leads["lead_id"].tolist())
        lead_lookup = leads.set_index("lead_id")
        selected = lead_lookup.loc[selected_ids]

        stages = rng.choice(
            values["deal_stages"],
            size=count,
            p=_ordered_weights(values["deal_stages"], rules["stage_weights"]),
        ).tolist()
        won_stage = mappings["quota_attainment"]["attained_stage"]
        non_won_closed_stage = next(
            stage for stage in rules["closed_stages"] if stage != won_stage
        )
        stages[0] = won_stage
        for position, stage in enumerate(
            (stage for stage in rules["required_stage_coverage"] if stage != won_stage),
            start=1,
        ):
            stages[position] = stage
        final_representative = self._representative_pool()[-1][0]
        for position, rep_name in enumerate(selected["rep_name"].tolist()):
            if rep_name == final_representative and stages[position] == won_stage:
                stages[position] = non_won_closed_stage

        created_values: list[str] = []
        close_values: list[str] = []
        expected_values: list[str] = []
        reference = _reference_datetime(self.settings.reference_today)
        activity_start = _configured_datetime(
            self.generation_rules["date_windows"]["deal_activity_start"]
        )
        first_target_period = date.fromisoformat(
            self.generation_rules["date_windows"]["target_period_start"]
        )
        for position, (lead_created, stage) in enumerate(
            zip(selected["created_at"], stages, strict=True)
        ):
            if position == 0:
                created = datetime.combine(
                    first_target_period - timedelta(days=31), time.min
                )
                close_date = first_target_period + timedelta(days=45)
                expected_date = first_target_period + timedelta(days=60)
            else:
                minimum_lag = (
                    int(rules["closed_date_lag_days"]["minimum"])
                    if stage in rules["closed_stages"]
                    else 0
                )
                created = _random_datetime(
                    date_rng,
                    max(activity_start, datetime.fromisoformat(lead_created)),
                    reference - timedelta(days=minimum_lag),
                )
                expected_offset = _random_int(
                    date_rng, rules["expected_close_offset_days"]
                )
                expected_date = created.date() + timedelta(days=expected_offset)
                if stage in rules["closed_stages"]:
                    max_lag = min(
                        int(rules["closed_date_lag_days"]["maximum"]),
                        (self.settings.reference_today - created.date()).days,
                    )
                    close_date = created.date() + timedelta(
                        days=int(
                            date_rng.integers(
                                int(rules["closed_date_lag_days"]["minimum"]),
                                max_lag + 1,
                            )
                        )
                    )
                else:
                    close_date = None
            created_values.append(_format_timestamp(created))
            close_values.append(close_date.isoformat() if close_date else "")
            expected_values.append(expected_date.isoformat())

        amount_rng = self.generator.rng_for("sales:deals:base_amount")
        amount_spec = self.settings.distributions["deal_amount"]
        amounts = _uniform_decimal_strings(
            amount_rng,
            count,
            int(amount_spec["min_amount"]),
            int(amount_spec["max_amount"]),
            int(amount_spec["scale"]),
        )
        return pd.DataFrame(
            {
                "deal_id": self.generator.make_integer_ids(count),
                "lead_id": selected_ids,
                "deal_name": [
                    f"{company} Sales Deal {index:05d}"
                    for index, company in enumerate(
                        selected["company_name"].tolist(), start=1
                    )
                ],
                "rep_name": selected["rep_name"].tolist(),
                "stage": stages,
                "deal_amount": amounts,
                "currency_code": [mappings["currency_code"]] * count,
                "close_date": close_values,
                "expected_close_date": expected_values,
                "created_at": created_values,
            },
            columns=SALES_COLUMN_CONTRACTS["deals"],
        )

    def generate_products(self) -> Any:
        """Generate a complete synthetic product catalog before imperfections.

        Products have unique deterministic SKUs, config-owned categories,
        positive fixed-scale list prices, USD currency, active-state values,
        and reproducible timestamps. Missing catalog prices are intentionally
        deferred to the imperfection stage.
        """

        pd = _require_pandas()
        count = self.generator.row_count("products")
        rng = self.generator.rng_for("sales:products:attributes")
        date_rng = self.generator.rng_for("sales:products:created_at")
        fake = self.generator.faker_for("sales:products:faker")
        values = self.sales_config["domain_values"]
        rules = self.generation_rules["products"]
        categories = rng.choice(values["product_categories"], size=count).tolist()
        _ensure_required_coverage(categories, values["product_categories"])
        price = rules["list_price"]
        active_probability = float(rules["active_probability"])
        return pd.DataFrame(
            {
                "product_id": self.generator.make_integer_ids(count),
                "sku": [f"SYN-SKU-{index:06d}" for index in range(1, count + 1)],
                "product_name": [
                    f"{fake.word().title()} {category} {index:04d}"
                    for index, category in enumerate(categories, start=1)
                ],
                "category": categories,
                "list_price": _uniform_decimal_strings(
                    rng,
                    count,
                    int(price["minimum_amount"]),
                    int(price["maximum_amount"]),
                    int(price["scale"]),
                ),
                "currency_code": [
                    self.sales_config["business_mappings"]["currency_code"]
                ]
                * count,
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[active_probability, 1 - active_probability],
                ).tolist(),
                "created_at": _random_timestamp_strings(
                    date_rng,
                    count,
                    _configured_datetime(
                        self.generation_rules["date_windows"]["entity_created_start"]
                    ),
                    _reference_datetime(self.settings.reference_today),
                ),
            },
            columns=SALES_COLUMN_CONTRACTS["products"],
        )

    def generate_quotations(self, deals: Any, products: Any) -> Any:
        """Generate quote lines forming the Deal-to-Product many-to-many bridge.

        Base pairs are unique and cover every deal and product. Seeded pairs
        guarantee at least one deal has multiple products and one product is
        used by multiple deals. Unit prices are captured from complete base
        product prices with a configured adjustment, so later missing catalog
        prices cannot erase quoted prices.
        """

        pd = _require_pandas()
        count = self.generator.row_count("quotations")
        pair_rng = self.generator.rng_for("sales:quotations:pairs")
        rng = self.generator.rng_for("sales:quotations:attributes")
        date_rng = self.generator.rng_for("sales:quotations:dates")
        rules = self.generation_rules["quotations"]
        deal_ids = [int(value) for value in deals["deal_id"]]
        product_ids = [int(value) for value in products["product_id"]]
        pairs = _unique_sales_pairs(pair_rng, deal_ids, product_ids, count)
        product_prices = {
            int(product_id): Decimal(price)
            for product_id, price in zip(
                products["product_id"], products["list_price"], strict=True
            )
        }
        deal_dates = {
            int(row.deal_id): (
                datetime.fromisoformat(row.created_at),
                (
                    datetime.combine(date.fromisoformat(row.close_date), time.max)
                    if row.close_date
                    else _reference_datetime(self.settings.reference_today)
                ),
            )
            for row in deals.itertuples(index=False)
        }

        quote_numbers = _quote_numbers(rng, pairs, rules["quote_number_group_size"])
        statuses = rng.choice(
            self.sales_config["domain_values"]["quote_statuses"], size=count
        ).tolist()
        _ensure_required_coverage(
            statuses, self.sales_config["domain_values"]["quote_statuses"]
        )
        quantities = rng.integers(
            int(rules["quantity"]["minimum"]),
            int(rules["quantity"]["maximum"]) + 1,
            size=count,
        ).tolist()
        discounts = _uniform_decimal_strings(
            rng,
            count,
            int(rules["discount_pct"]["minimum"]),
            int(rules["discount_pct"]["maximum"]),
            int(rules["discount_pct"]["scale"]),
        )
        adjustment = rules["unit_price_adjustment_pct"]
        adjustment_values = rng.integers(
            int(adjustment["minimum"]),
            int(adjustment["maximum"]) + 1,
            size=count,
        )
        unit_prices = [
            _decimal_string(
                product_prices[product_id]
                * (Decimal(100 + int(adjustment_pct)) / Decimal(100)),
                2,
            )
            for (_, product_id), adjustment_pct in zip(
                pairs, adjustment_values, strict=True
            )
        ]

        created_values: list[str] = []
        quoted_values: list[str] = []
        lag = rules["created_to_quoted_lag_days"]
        for deal_id, _ in pairs:
            deal_created, quote_end = deal_dates[deal_id]
            created = _random_datetime(date_rng, deal_created, quote_end)
            max_lag_days = min(
                int(lag["maximum"]),
                max(0, (quote_end - created).days),
            )
            lag_days = int(date_rng.integers(int(lag["minimum"]), max_lag_days + 1))
            quoted = min(
                quote_end,
                created
                + timedelta(
                    days=lag_days,
                    seconds=int(date_rng.integers(0, 86400)),
                ),
            )
            created_values.append(_format_timestamp(created))
            quoted_values.append(_format_timestamp(quoted))

        return pd.DataFrame(
            {
                "quotation_id": self.generator.make_integer_ids(count),
                "deal_id": [deal_id for deal_id, _ in pairs],
                "product_id": [product_id for _, product_id in pairs],
                "quote_number": quote_numbers,
                "quantity": quantities,
                "unit_price": unit_prices,
                "discount_pct": discounts,
                "quote_status": statuses,
                "quoted_at": quoted_values,
                "created_at": created_values,
            },
            columns=SALES_COLUMN_CONTRACTS["quotations"],
        )

    def generate_targets(self, deals: Any) -> Any:
        """Generate non-overlapping quarterly quotas for every representative.

        Each configured representative receives the configured number of
        contiguous start-inclusive/end-exclusive periods and retains the same
        territory used by leads. Quota values are positive USD amounts; their
        statistical shaping is applied later by the distribution stage.
        """

        pd = _require_pandas()
        if deals.empty:
            raise ValueError("Sales targets require generated deals")
        count = self.generator.row_count("targets")
        rng = self.generator.rng_for("sales:targets:attributes")
        date_rng = self.generator.rng_for("sales:targets:periods")
        representatives = self._representative_pool()
        rules = self.generation_rules["targets"]
        periods_per_rep = int(
            rules["periods_per_representative"][self.settings.profile]
        )
        period_months = int(rules["period_months"])
        first_period = date.fromisoformat(
            self.generation_rules["date_windows"]["target_period_start"]
        )
        amount = self.settings.distributions["quota_amount"]
        rows: list[dict[str, Any]] = []
        for rep_name, territory in representatives:
            for period_index in range(periods_per_rep):
                period_start = _add_months(first_period, period_index * period_months)
                period_end = _add_months(period_start, period_months)
                lead_days = _random_int(date_rng, rules["creation_lead_days"])
                rows.append(
                    {
                        "rep_name": rep_name,
                        "territory": territory,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "created_at": _format_timestamp(
                            datetime.combine(
                                period_start - timedelta(days=lead_days), time.min
                            )
                        ),
                    }
                )
        if len(rows) != count:
            raise ValueError(
                "Generated Sales target capacity differs from configured row count"
            )
        quota_values = _uniform_decimal_strings(
            rng,
            count,
            int(amount["min_amount"]),
            int(amount["max_amount"]),
            int(amount["scale"]),
        )
        return pd.DataFrame(
            {
                "target_id": self.generator.make_integer_ids(count),
                "rep_name": [row["rep_name"] for row in rows],
                "territory": [row["territory"] for row in rows],
                "period_start": [row["period_start"] for row in rows],
                "period_end": [row["period_end"] for row in rows],
                "quota_amount": quota_values,
                "currency_code": [
                    self.sales_config["business_mappings"]["currency_code"]
                ]
                * count,
                "created_at": [row["created_at"] for row in rows],
            },
            columns=SALES_COLUMN_CONTRACTS["targets"],
        )

    def _representative_pool(self) -> list[tuple[str, str]]:
        """Return cached unique representative names and fixed territories."""

        if self._representatives is None:
            count = int(
                self.generation_rules["representatives"]["pool_sizes"][
                    self.settings.profile
                ]
            )
            fake = self.generator.faker_for("sales:representatives:faker")
            rng = self.generator.rng_for("sales:representatives:territories")
            names = _unique_faker_names(fake, count)
            territories = rng.choice(
                self.sales_config["domain_values"]["territories"], size=count
            ).tolist()
            _ensure_required_coverage(
                territories, self.sales_config["domain_values"]["territories"]
            )
            self._representatives = list(zip(names, territories, strict=True))
        return self._representatives

    def _deal_lead_ids(self, lead_ids: list[int]) -> list[int]:
        """Select and cache one unique source lead for every base deal."""

        if self._selected_lead_ids is None:
            count = self.generator.row_count("deals")
            if count > len(lead_ids):
                raise ValueError("Sales deals cannot exceed available source leads")
            rng = self.generator.rng_for("sales:deals:relationships")
            self._selected_lead_ids = [
                int(value)
                for value in rng.choice(lead_ids, size=count, replace=False).tolist()
            ]
        return self._selected_lead_ids


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for Sales generation") from exc
    return pd


def _reference_datetime(reference_today: date) -> datetime:
    return datetime.combine(reference_today, time(23, 59, 59))


def _configured_datetime(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.min)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _random_datetime(rng: Any, start: datetime, end: datetime) -> datetime:
    if end < start:
        raise ValueError("Random timestamp range end cannot precede start")
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=int(rng.integers(0, seconds + 1)))


def _random_timestamp_strings(
    rng: Any,
    count: int,
    start: datetime,
    end: datetime,
) -> list[str]:
    return [_format_timestamp(_random_datetime(rng, start, end)) for _ in range(count)]


def _random_int(rng: Any, configured_range: dict[str, Any]) -> int:
    return int(
        rng.integers(
            int(configured_range["minimum"]),
            int(configured_range["maximum"]) + 1,
        )
    )


def _ordered_weights(values: list[str], weights: dict[str, Any]) -> list[float]:
    return [float(weights[value]) for value in values]


def _ensure_required_coverage(values: list[Any], required: list[Any]) -> None:
    for position, value in enumerate(required[: len(values)]):
        values[position] = value


def _normalized_company_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", normalized).strip()


def _unique_faker_names(fake: Any, count: int) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    while len(names) < count:
        name = re.sub(r"\s+", " ", fake.name()).strip()
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _uniform_decimal_strings(
    rng: Any,
    count: int,
    minimum: int,
    maximum: int,
    scale: int,
) -> list[str]:
    multiplier = 10**scale
    values = rng.integers(
        minimum * multiplier,
        (maximum * multiplier) + 1,
        size=count,
    )
    return [format_fixed_decimal(int(value), scale) for value in values]


def _decimal_string(value: Decimal, scale: int) -> str:
    quantizer = Decimal(1).scaleb(-scale)
    return f"{value.quantize(quantizer):.{scale}f}"


def _unique_sales_pairs(
    rng: Any,
    deal_ids: list[int],
    product_ids: list[int],
    count: int,
) -> list[tuple[int, int]]:
    if not deal_ids or not product_ids:
        raise ValueError("Sales quotation pairs require deals and products")
    capacity = len(deal_ids) * len(product_ids)
    if count > capacity:
        raise ValueError("Sales quotation target exceeds unique deal/product capacity")

    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def append(pair: tuple[int, int]) -> None:
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)

    append((deal_ids[0], product_ids[0]))
    if len(product_ids) > 1:
        append((deal_ids[0], product_ids[1]))
    if len(deal_ids) > 1:
        append((deal_ids[1], product_ids[0]))
    for index, deal_id in enumerate(deal_ids):
        append((deal_id, product_ids[index % len(product_ids)]))
    for index, product_id in enumerate(product_ids):
        append((deal_ids[(index + 1) % len(deal_ids)], product_id))
    while len(pairs) < count:
        remaining = count - len(pairs)
        batch_size = max(remaining * 2, 32)
        sampled_deals = rng.choice(deal_ids, size=batch_size)
        sampled_products = rng.choice(product_ids, size=batch_size)
        for deal_id, product_id in zip(sampled_deals, sampled_products, strict=True):
            append((int(deal_id), int(product_id)))
            if len(pairs) == count:
                break
    return pairs


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
            remaining[deal_id] = _random_int(rng, group_size)
        numbers.append(f"SYN-Q-{deal_id:06d}-{counters[deal_id]:03d}")
        remaining[deal_id] -= 1
    return numbers


def _add_months(value: date, months: int) -> date:
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, value.day)


__all__ = [
    "SALES_COLUMN_CONTRACTS",
    "SalesBaseEntityGenerator",
    "build_sales_column_contracts",
]
