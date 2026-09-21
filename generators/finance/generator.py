"""Finance dataframe contracts and deterministic base generation."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import validate_column_contracts
from generators.core.progress import ProgressReporter
from generators.finance.config import load_base_config
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.decimal_policy import allocate_money
from generators.finance.decimal_policy import format_fx_rate
from generators.finance.decimal_policy import format_money
from generators.finance.decimal_policy import MONEY_SCALE
from generators.finance.decimal_policy import quantize_fx_rate
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.generated_tables import (
    validate_finance_generated_tables,
)


def build_finance_column_contracts(
    finance_config: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Build ordered table and column contracts from Finance configuration.

    The assembled Finance schema configuration is the sole source of dataframe
    table and column order. Every later generation, distribution, imperfection,
    validation, and CSV stage can therefore consume one contract that remains
    aligned with the canonical DDL and signed CSV header specification.
    """

    config = finance_config if finance_config is not None else load_finance_config()
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


FINANCE_COLUMN_CONTRACTS = build_finance_column_contracts()


class FinanceBaseEntityGenerator:
    """Generate clean deterministic Finance tables from frozen configuration.

    The generator creates a synthetic chart of accounts, a complete FX
    currency/date grid, transactions backed by available FX keys, exact
    double-entry ledger lines, and account-period budgets. FX rows are built
    before transactions internally, but the returned mapping follows the
    signed release order in :data:`FINANCE_COLUMN_CONTRACTS`.

    Every random choice uses a stable named stream. Monetary calculations use
    the Finance decimal policy and integer ten-thousandths, so generation does
    not depend on wall-clock time, external rates, or binary floats.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "finance":
            raise ValueError(
                "FinanceBaseEntityGenerator only supports the finance domain"
            )
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.base_config = load_base_config()
        self.finance_config = load_finance_config()
        self.generation_rules = self.finance_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "FinanceBaseEntityGenerator":
        """Create a validated Finance base generator for one profile."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_tables(self) -> dict[str, Any]:
        """Generate all five clean tables and return them in release order."""

        accounts = self._generate_and_report("accounts", self.generate_accounts)
        # FX is generated before transactions so every transaction can select
        # an existing analytical currency/date key.
        fx_rates = self._generate_and_report("fx_rates", self.generate_fx_rates)
        transactions = self._generate_and_report(
            "transactions", lambda: self.generate_transactions(fx_rates)
        )
        ledger_entries = self._generate_and_report(
            "ledger_entries",
            lambda: self.generate_ledger_entries(accounts, transactions),
        )
        budgets = self._generate_and_report(
            "budgets", lambda: self.generate_budgets(accounts)
        )
        tables = {
            "accounts": accounts,
            "transactions": transactions,
            "ledger_entries": ledger_entries,
            "budgets": budgets,
            "fx_rates": fx_rates,
        }
        self._report("Validating base Finance tables")
        validate_finance_generated_tables(self.generator, tables)
        self._report("Base Finance generation complete")
        return tables

    def _generate_and_report(self, table_name: str, generate: Any) -> Any:
        """Generate one table and report its shape when progress is enabled."""

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
        """Generate an acyclic, currency-capable synthetic chart of accounts.

        Account types determine compatible subtypes and normal balances.
        Parent accounts always precede their children and share the child's
        account type. The first deterministic account block guarantees at
        least two active posting accounts for every supported source currency.
        """

        pd = _require_pandas()
        count = self.generator.row_count("accounts")
        attribute_rng = self.generator.rng_for("finance:accounts:attributes")
        hierarchy_rng = self.generator.rng_for("finance:accounts:hierarchy")
        date_rng = self.generator.rng_for("finance:accounts:dates")
        values = self.finance_config["domain_values"]
        mappings = self.finance_config["business_mappings"]
        rules = self.generation_rules["accounts"]

        account_types = values["account_types"]
        account_type_values = attribute_rng.choice(
            account_types,
            size=count,
            p=_ordered_weights(account_types, rules["type_weights"]),
        ).tolist()
        _ensure_cycle_coverage(account_type_values, account_types)

        currencies = values["source_currencies"]
        currency_values = attribute_rng.choice(
            currencies,
            size=count,
            p=_ordered_weights(currencies, rules["currency_weights"]),
        ).tolist()
        required_currency_slots = currencies * 2
        _ensure_cycle_coverage(currency_values, required_currency_slots)

        subtypes = [
            str(attribute_rng.choice(mappings["account_subtypes"][account_type]))
            for account_type in account_type_values
        ]
        account_ids = self.generator.make_integer_ids(count)
        root_count = max(
            len(account_types),
            round(count * float(rules["hierarchy"]["root_fraction"])),
        )
        ids_by_type: dict[str, list[int]] = {
            account_type: [] for account_type in account_types
        }
        parent_ids: list[int | str] = []
        for position, (account_id, account_type) in enumerate(
            zip(account_ids, account_type_values, strict=True)
        ):
            candidates = ids_by_type[account_type]
            if position < root_count or not candidates:
                parent_ids.append("")
            else:
                parent_ids.append(int(hierarchy_rng.choice(candidates)))
            candidates.append(account_id)

        active_values = attribute_rng.random(count) < float(rules["active_probability"])
        active_values[: min(count, len(required_currency_slots))] = True
        if count > len(required_currency_slots):
            active_values[-1] = False

        created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        reference = datetime.combine(self.settings.reference_today, time.max).replace(
            microsecond=0
        )
        created_at = _random_timestamp_strings(
            date_rng, count, created_start, reference
        )
        account_numbers = [f"SYN-ACC-{account_id:06d}" for account_id in account_ids]

        return pd.DataFrame(
            {
                "account_id": account_ids,
                "account_number": account_numbers,
                "account_name": [
                    f"Synthetic {subtype} Account {account_id:06d}"
                    for account_id, subtype in zip(account_ids, subtypes, strict=True)
                ],
                "account_type": account_type_values,
                "account_subtype": subtypes,
                "currency_code": currency_values,
                "parent_account_id": parent_ids,
                "normal_balance": [
                    mappings["normal_balance_by_account_type"][account_type]
                    for account_type in account_type_values
                ],
                "is_active": active_values.tolist(),
                "created_at": created_at,
            },
            columns=FINANCE_COLUMN_CONTRACTS["accounts"],
        )

    def generate_fx_rates(self) -> Any:
        """Generate the complete synthetic source-currency-to-USD rate grid.

        Each configured pair receives one row for every profile date. USD/USD
        is fixed at exactly 1.000000; other pairs follow deterministic bounded
        Decimal movement around config-owned synthetic anchors. Rates remain
        populated in the clean base stage.
        """

        pd = _require_pandas()
        movement_rng = self.generator.rng_for("finance:fx_rates:movement")
        attribute_rng = self.generator.rng_for("finance:fx_rates:attributes")
        rules = self.generation_rules
        movement = rules["fx_daily_movement"]
        pairs = rules["fx_calendar"]["currency_pairs"]
        rate_dates = self._rate_dates()
        anchors = self.finance_config["business_mappings"]["fx_anchor_rates"]
        sources = self.finance_config["domain_values"]["rate_sources"]
        maximum_bps = int(movement["maximum_daily_change_bps"])
        mean_reversion = Decimal(movement["mean_reversion_fraction"])
        estimated_probability = float(rules["fx_rates"]["estimated_probability"])

        rows: list[dict[str, Any]] = []
        rate_id = 1
        for pair in pairs:
            from_currency, to_currency = pair.split("/", 1)
            anchor = Decimal(anchors[pair])
            previous = anchor
            for rate_date in rate_dates:
                if pair == movement["identity_pair"]:
                    rate = Decimal(movement["identity_rate"])
                else:
                    basis_points = int(
                        movement_rng.integers(-maximum_bps, maximum_bps + 1)
                    )
                    shock = anchor * Decimal(basis_points) / Decimal(10000)
                    previous = quantize_fx_rate(
                        previous - ((previous - anchor) * mean_reversion) + shock
                    )
                    rate = previous if previous > 0 else quantize_fx_rate(anchor)
                estimated = bool(attribute_rng.random() < estimated_probability)
                rows.append(
                    {
                        "rate_id": rate_id,
                        "from_currency": from_currency,
                        "to_currency": to_currency,
                        "rate_date": rate_date,
                        "rate": format_fx_rate(rate),
                        "rate_source": sources[1] if estimated else sources[0],
                        "is_estimated": estimated,
                        "created_at": f"{rate_date}T00:00:00",
                    }
                )
                rate_id += 1

        return pd.DataFrame(rows, columns=FINANCE_COLUMN_CONTRACTS["fx_rates"])

    def generate_transactions(self, fx_rates: Any) -> Any:
        """Generate positive source-currency transactions with valid FX keys.

        Base transactions use only regular calendar dates. Boundary dates are
        reserved for controlled imperfection injection, while every generated
        source/target/date combination already has a corresponding FX row.
        Posted timestamps are on or after the transaction date and reversals
        are represented explicitly for downstream ledger orientation.
        """

        pd = _require_pandas()
        count = self.generator.row_count("transactions")
        attribute_rng = self.generator.rng_for("finance:transactions:attributes")
        date_rng = self.generator.rng_for("finance:transactions:dates")
        amount_rng = self.generator.rng_for("finance:transactions:amounts")
        rules = self.generation_rules["transactions"]
        values = self.finance_config["domain_values"]

        boundary_dates = set(self.base_config["imperfections"]["boundary_dates"])
        regular_keys = [
            (row.from_currency, row.to_currency, row.rate_date)
            for row in fx_rates.itertuples(index=False)
            if row.rate_date not in boundary_dates
        ]
        key_order = attribute_rng.permutation(len(regular_keys)).tolist()
        ordered_keys = [regular_keys[int(position)] for position in key_order]
        selected_keys = [
            ordered_keys[index % len(ordered_keys)] for index in range(count)
        ]

        systems = values["source_systems"]
        source_systems = attribute_rng.choice(
            systems,
            size=count,
            p=_ordered_weights(systems, rules["source_system_weights"]),
        ).tolist()
        _ensure_cycle_coverage(source_systems, systems)

        amount_spec = self.settings.distributions["transaction_amount"]
        multiplier = 10 ** int(amount_spec["scale"])
        amount_units = amount_rng.integers(
            int(amount_spec["min_amount"]) * multiplier,
            (int(amount_spec["max_amount"]) * multiplier) + 1,
            size=count,
        )
        reversed_values = attribute_rng.random(count) < float(
            rules["reversed_probability"]
        )
        if count > 1:
            reversed_values[0] = True
            reversed_values[1] = False

        lag = rules["posted_lag_days"]
        posted_at: list[str] = []
        for _, _, transaction_date in selected_keys:
            day_lag = int(
                date_rng.integers(int(lag["minimum"]), int(lag["maximum"]) + 1)
            )
            second = int(date_rng.integers(0, 24 * 60 * 60))
            posted = datetime.combine(
                date.fromisoformat(transaction_date) + timedelta(days=day_lag),
                time.min,
            ) + timedelta(seconds=second)
            posted_at.append(_format_timestamp(posted))

        transaction_ids = self.generator.make_integer_ids(count)
        return pd.DataFrame(
            {
                "transaction_id": transaction_ids,
                "transaction_date": [key[2] for key in selected_keys],
                "description": [
                    f"Synthetic {system} transaction {transaction_id:08d}"
                    for transaction_id, system in zip(
                        transaction_ids, source_systems, strict=True
                    )
                ],
                "source_system": source_systems,
                "source_currency": [key[0] for key in selected_keys],
                "target_currency": [key[1] for key in selected_keys],
                "total_amount": [
                    format_money(Decimal(int(units)).scaleb(-MONEY_SCALE))
                    for units in amount_units
                ],
                "reversed": reversed_values.tolist(),
                "posted_at": posted_at,
            },
            columns=FINANCE_COLUMN_CONTRACTS["transactions"],
        )

    def generate_ledger_entries(self, accounts: Any, transactions: Any) -> Any:
        """Generate exact double-entry lines for the posted transaction subset.

        Five percent of transactions are deterministically left without ledger
        rows for the required LEFT JOIN path. Every remaining transaction gets
        one or more debit/credit pairs whose integer minor-unit allocations sum
        exactly to the transaction amount on both sides. Reversed transactions
        swap line orientation rather than applying a later sign multiplier.
        """

        pd = _require_pandas()
        target_rows = self.generator.row_count("ledger_entries")
        selection_rng = self.generator.rng_for(
            "finance:ledger_entries:transaction_selection"
        )
        allocation_rng = self.generator.rng_for("finance:ledger_entries:allocation")
        account_rng = self.generator.rng_for("finance:ledger_entries:accounts")
        attribute_rng = self.generator.rng_for("finance:ledger_entries:attributes")
        date_rng = self.generator.rng_for("finance:ledger_entries:dates")
        rules = self.generation_rules

        transaction_ids = [int(value) for value in transactions["transaction_id"]]
        unposted_count = round(
            len(transaction_ids)
            * float(rules["transactions"]["unposted_to_ledger_fraction"])
        )
        unposted_ids = set(
            int(value)
            for value in selection_rng.choice(
                transaction_ids, size=unposted_count, replace=False
            )
        )
        posted_ids = [
            transaction_id
            for transaction_id in transaction_ids
            if transaction_id not in unposted_ids
        ]
        total_pairs = target_rows // 2
        if total_pairs < len(posted_ids):
            raise ValueError("Ledger row target cannot cover all posted transactions")

        pair_counts = {transaction_id: 1 for transaction_id in posted_ids}
        extra_pair_count = total_pairs - len(posted_ids)
        if extra_pair_count:
            order = allocation_rng.permutation(posted_ids).tolist()
            for index in range(extra_pair_count):
                pair_counts[int(order[index % len(order)])] += 1

        active_accounts = accounts[accounts["is_active"]]
        account_ids_by_currency = {
            currency: [int(value) for value in group["account_id"]]
            for currency, group in active_accounts.groupby("currency_code", sort=False)
        }
        transaction_lookup = transactions.set_index("transaction_id")
        posting_types = [
            value
            for value in self.finance_config["domain_values"]["posting_types"]
            if value != "Reversal"
        ]
        lag = rules["ledger_entries"]["created_lag_hours"]

        rows: list[dict[str, Any]] = []
        entry_id = 1
        for transaction_id in posted_ids:
            transaction = transaction_lookup.loc[transaction_id]
            currency = transaction["source_currency"]
            account_pool = account_ids_by_currency.get(currency, [])
            if len(account_pool) < 2:
                raise ValueError(
                    f"Finance needs two active posting accounts for {currency}"
                )
            allocations = allocate_money(
                transaction["total_amount"], pair_counts[transaction_id]
            )
            posted_at = datetime.fromisoformat(transaction["posted_at"])
            line_number = 1
            for allocation in allocations:
                debit_account, credit_account = (
                    int(value)
                    for value in account_rng.choice(
                        account_pool, size=2, replace=False
                    ).tolist()
                )
                posting_type = (
                    "Reversal"
                    if bool(transaction["reversed"])
                    else str(attribute_rng.choice(posting_types))
                )
                created = posted_at + timedelta(
                    hours=int(
                        date_rng.integers(int(lag["minimum"]), int(lag["maximum"]) + 1)
                    ),
                    seconds=int(date_rng.integers(0, 60 * 60)),
                )
                line_specs = [
                    (debit_account, format_money(allocation), ""),
                    (credit_account, "", format_money(allocation)),
                ]
                if bool(transaction["reversed"]):
                    line_specs = [
                        (debit_account, "", format_money(allocation)),
                        (credit_account, format_money(allocation), ""),
                    ]
                for account_id, debit_amount, credit_amount in line_specs:
                    rows.append(
                        {
                            "entry_id": entry_id,
                            "transaction_id": transaction_id,
                            "account_id": account_id,
                            "line_number": line_number,
                            "debit_amount": debit_amount,
                            "credit_amount": credit_amount,
                            "currency_code": currency,
                            "posting_type": posting_type,
                            "created_at": _format_timestamp(created),
                        }
                    )
                    entry_id += 1
                    line_number += 1

        return pd.DataFrame(rows, columns=FINANCE_COLUMN_CONTRACTS["ledger_entries"])

    def generate_budgets(self, accounts: Any) -> Any:
        """Generate USD account budgets over non-overlapping fiscal periods."""

        pd = _require_pandas()
        amount_rng = self.generator.rng_for("finance:budgets:amounts")
        attribute_rng = self.generator.rng_for("finance:budgets:attributes")
        date_rng = self.generator.rng_for("finance:budgets:dates")
        rules = self.generation_rules["budgets"]
        period_count = int(rules["periods_per_account"][self.settings.profile])
        period_offsets = rules["period_offsets"][self.settings.profile]
        period_months = int(rules["period_months"])
        first_period = date.fromisoformat(
            self.generation_rules["date_windows"]["budget_period_start"]
        )
        scenarios = self.finance_config["domain_values"]["budget_scenarios"]
        scenario_values = attribute_rng.choice(
            scenarios,
            size=len(accounts) * period_count,
            p=_ordered_weights(scenarios, rules["scenario_weights"]),
        ).tolist()
        _ensure_cycle_coverage(scenario_values, scenarios)

        amount_spec = self.settings.distributions["budget_amount"]
        multiplier = 10 ** int(amount_spec["scale"])
        amount_units = amount_rng.integers(
            int(amount_spec["min_amount"]) * multiplier,
            (int(amount_spec["max_amount"]) * multiplier) + 1,
            size=len(accounts) * period_count,
        )
        lead = rules["creation_lead_days"]

        rows: list[dict[str, Any]] = []
        budget_id = 1
        position = 0
        for account_id in accounts["account_id"]:
            for period_index in period_offsets:
                period_start = _add_months(first_period, period_index * period_months)
                period_end = _add_months(period_start, period_months)
                lead_days = int(
                    date_rng.integers(int(lead["minimum"]), int(lead["maximum"]) + 1)
                )
                created = datetime.combine(
                    period_start - timedelta(days=lead_days), time.min
                )
                rows.append(
                    {
                        "budget_id": budget_id,
                        "account_id": int(account_id),
                        "fiscal_year": period_start.year,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "budget_amount": format_money(
                            Decimal(int(amount_units[position])).scaleb(-MONEY_SCALE)
                        ),
                        "currency_code": self.finance_config["business_mappings"][
                            "reporting_currency"
                        ],
                        "scenario": scenario_values[position],
                        "created_at": _format_timestamp(created),
                    }
                )
                budget_id += 1
                position += 1

        return pd.DataFrame(rows, columns=FINANCE_COLUMN_CONTRACTS["budgets"])

    def _rate_dates(self) -> list[str]:
        """Return deterministic regular and configured boundary FX dates."""

        calendar = self.generation_rules["fx_calendar"]
        regular_count = int(calendar["regular_date_counts"][self.settings.profile])
        start = date.fromisoformat(
            self.generation_rules["date_windows"]["transaction_activity_start"]
        )
        end = self.settings.reference_today
        boundaries = list(self.base_config["imperfections"]["boundary_dates"])
        boundary_set = set(boundaries)
        candidates = [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).isoformat() not in boundary_set
        ]
        if regular_count > len(candidates):
            raise ValueError("Finance FX calendar lacks enough unique regular dates")
        rng = self.generator.rng_for("finance:fx_rates:calendar")
        positions = sorted(
            int(value)
            for value in rng.choice(
                len(candidates), size=regular_count, replace=False
            ).tolist()
        )
        regular_dates = [candidates[position] for position in positions]
        return regular_dates + boundaries


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Install requirements.txt."
        ) from exc
    return pd


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


def _ordered_weights(values: list[str], weights: dict[str, Any]) -> list[float]:
    return [float(weights[value]) for value in values]


def _ensure_cycle_coverage(values: list[Any], required: list[Any]) -> None:
    for position, value in enumerate(required[: len(values)]):
        values[position] = value


def _add_months(value: date, months: int) -> date:
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, value.day)


__all__ = [
    "FINANCE_COLUMN_CONTRACTS",
    "FinanceBaseEntityGenerator",
    "build_finance_column_contracts",
]
