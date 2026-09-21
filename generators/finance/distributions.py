"""Deterministic distribution application for clean Finance entities."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.distributions import gaussian_mixture_offsets
from generators.core.distributions import gaussian_mixture_timestamps
from generators.core.distributions import pareto_decimal_strings
from generators.core.distributions import poisson_weights
from generators.core.progress import ProgressReporter
from generators.finance.config import load_base_config
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.decimal_policy import allocate_money
from generators.finance.decimal_policy import format_fx_rate
from generators.finance.decimal_policy import format_money
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.distributed_tables import (
    validate_finance_distributed_tables,
)
from generators.finance.validators.generated_tables import (
    validate_finance_generated_tables,
)


class FinanceDistributionApplier:
    """Apply realistic distributions while preserving Finance invariants.

    Transaction and budget amounts receive bounded Pareto values. Posting-pair
    counts are redistributed with Poisson-derived weights, after which ledger
    rows are rebuilt from the new transaction amounts using exact scale-4
    allocation. Gaussian-mixture sampling clusters mutable dates without
    changing fiscal-period or FX-calendar keys. FX values are regenerated with
    the configured bounded Decimal movement and USD identity anchor.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceDistributionApplier only supports finance")
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
    ) -> "FinanceDistributionApplier":
        """Create an applier from validated Finance profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_distributed_tables(self) -> dict[str, Any]:
        """Generate fresh clean Finance tables and apply all distributions."""

        base_tables = FinanceBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        return self.apply_to_tables(base_tables)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return distributed deep copies of valid clean Finance tables."""

        self._report("Applying Finance distributions")
        validate_finance_generated_tables(self.generator, tables)
        distributed = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        self._apply_amount_distribution(
            distributed["transactions"],
            column_name="total_amount",
            settings_key="transaction_amount",
        )
        self._report("Applied Pareto transaction-amount distribution")
        self._apply_amount_distribution(
            distributed["budgets"],
            column_name="budget_amount",
            settings_key="budget_amount",
        )
        self._report("Applied Pareto budget-amount distribution")
        self._apply_date_distributions(distributed, source_tables=tables)
        self._report("Applied Gaussian-mixture date distributions")
        self._apply_fx_movement(distributed["fx_rates"])
        self._report("Applied deterministic FX-rate movement")
        distributed["ledger_entries"] = self._rebuild_ledger_entries(
            distributed,
            source_ledger=tables["ledger_entries"],
        )
        self._report("Applied Poisson posting frequency and rebuilt ledger")

        self._report("Validating distributed Finance tables")
        validate_finance_distributed_tables(
            self.generator,
            distributed,
            source_tables=tables,
        )
        self._report("Finance distribution application complete")
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
        """Replace one amount column with bounded fixed-scale Pareto values."""

        spec = self.settings.distributions[settings_key]
        rng = self.generator.rng_for(
            f"distribution:finance:{settings_key}:{column_name}"
        )
        table[column_name] = [
            format_money(value)
            for value in pareto_decimal_strings(
                rng,
                count=len(table),
                alpha=float(spec["alpha"]),
                min_amount=int(spec["min_amount"]),
                max_amount=int(spec["max_amount"]),
                scale=int(spec["scale"]),
            )
        ]

    def _apply_date_distributions(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Cluster mutable dates and rebuild dependent chronology."""

        spec = self.settings.distributions["date_clustering"]
        entity_start = date.fromisoformat(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        reference = self.settings.reference_today

        account_rng = self.generator.rng_for(
            "distribution:finance:accounts:created_at"
        )
        tables["accounts"]["created_at"] = gaussian_mixture_timestamps(
            account_rng,
            count=len(tables["accounts"]),
            start=entity_start,
            end=reference,
            component_centers=spec["component_centers"],
            component_weights=spec["component_weights"],
            std_fraction=float(spec["std_fraction"]),
        )

        self._cluster_transaction_dates(tables, source_tables, spec)
        self._cluster_budget_created_at(tables["budgets"], spec)
        self._update_fx_created_at(tables["fx_rates"])

    def _cluster_transaction_dates(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
        spec: dict[str, Any],
    ) -> None:
        """Cluster transaction dates over valid regular FX calendar keys."""

        transactions = tables["transactions"]
        boundaries = set(self.base_config["imperfections"]["boundary_dates"])
        regular_dates = sorted(
            set(tables["fx_rates"]["rate_date"]) - boundaries
        )
        rng = self.generator.rng_for("distribution:finance:transactions:dates")
        offsets = gaussian_mixture_offsets(
            rng,
            count=len(transactions),
            day_span=len(regular_dates) - 1,
            component_centers=spec["component_centers"],
            component_weights=spec["component_weights"],
            std_fraction=float(spec["std_fraction"]),
        )
        selected_dates = [regular_dates[int(offset)] for offset in offsets]

        # Keep one known ledger-backed transaction in a matched budget period.
        anchor_id = int(source_tables["ledger_entries"].iloc[0]["transaction_id"])
        anchor_position = int(
            transactions.index[transactions["transaction_id"] == anchor_id][0]
        )
        selected_dates[anchor_position] = regular_dates[0]
        transactions["transaction_date"] = selected_dates

        lag = self.generation_rules["transactions"]["posted_lag_days"]
        posted_values: list[str] = []
        for transaction_date in selected_dates:
            day_lag = int(
                rng.integers(int(lag["minimum"]), int(lag["maximum"]) + 1)
            )
            second = int(rng.integers(0, 24 * 60 * 60))
            posted = datetime.combine(
                date.fromisoformat(transaction_date) + timedelta(days=day_lag),
                time.min,
            ) + timedelta(seconds=second)
            posted_values.append(_format_timestamp(posted))
        transactions["posted_at"] = posted_values

    def _cluster_budget_created_at(
        self,
        budgets: Any,
        spec: dict[str, Any],
    ) -> None:
        """Cluster budget creation while keeping it before each period."""

        start = date.fromisoformat(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        latest = max(date.fromisoformat(value) for value in budgets["period_start"])
        rng = self.generator.rng_for("distribution:finance:budgets:created_at")
        samples = gaussian_mixture_timestamps(
            rng,
            count=len(budgets),
            start=start,
            end=latest,
            component_centers=spec["component_centers"],
            component_weights=spec["component_weights"],
            std_fraction=float(spec["std_fraction"]),
        )
        budgets["created_at"] = [
            _format_timestamp(
                min(
                    datetime.fromisoformat(sample),
                    datetime.combine(
                        date.fromisoformat(period_start) - timedelta(days=1),
                        time.max,
                    ),
                )
            )
            for sample, period_start in zip(
                samples, budgets["period_start"], strict=True
            )
        ]

    def _update_fx_created_at(self, fx_rates: Any) -> None:
        """Vary FX timestamps while preserving each effective rate date."""

        rng = self.generator.rng_for("distribution:finance:fx_rates:created_at")
        seconds = rng.integers(0, 24 * 60 * 60, size=len(fx_rates))
        fx_rates["created_at"] = [
            _format_timestamp(
                datetime.combine(date.fromisoformat(rate_date), time.min)
                + timedelta(seconds=int(second))
            )
            for rate_date, second in zip(fx_rates["rate_date"], seconds, strict=True)
        ]

    def _apply_fx_movement(self, fx_rates: Any) -> None:
        """Regenerate rates with bounded Decimal movement and exact identity."""

        rules = self.generation_rules["fx_daily_movement"]
        anchors = self.finance_config["business_mappings"]["fx_anchor_rates"]
        rng = self.generator.rng_for("distribution:finance:fx_rates:movement")
        maximum_bps = int(rules["maximum_daily_change_bps"])
        mean_reversion = Decimal(rules["mean_reversion_fraction"])
        rates: list[str] = []
        previous_by_pair: dict[str, Decimal] = {}
        for row in fx_rates.itertuples(index=False):
            pair = f"{row.from_currency}/{row.to_currency}"
            anchor = Decimal(anchors[pair])
            if pair == rules["identity_pair"]:
                rate = Decimal(rules["identity_rate"])
            else:
                previous = previous_by_pair.get(pair, anchor)
                basis_points = int(rng.integers(-maximum_bps, maximum_bps + 1))
                shock = anchor * Decimal(basis_points) / Decimal(10000)
                rate = previous - ((previous - anchor) * mean_reversion) + shock
                if rate <= 0:
                    rate = anchor
            formatted = format_fx_rate(rate)
            previous_by_pair[pair] = Decimal(formatted)
            rates.append(formatted)
        fx_rates["rate"] = rates

    def _rebuild_ledger_entries(
        self,
        tables: dict[str, Any],
        source_ledger: Any,
    ) -> Any:
        """Rebuild exact ledger lines using Poisson posting-pair frequencies."""

        pd = _require_pandas()
        transactions = tables["transactions"]
        accounts = tables["accounts"]
        posted_ids = sorted(set(int(value) for value in source_ledger["transaction_id"]))
        total_pairs = self.generator.row_count("ledger_entries") // 2
        frequency_rng = self.generator.rng_for(
            "distribution:finance:ledger_entries:frequency"
        )
        pair_counts = _exact_poisson_pair_counts(
            frequency_rng,
            item_count=len(posted_ids),
            total_pairs=total_pairs,
            lam=float(self.settings.distributions["ledger_line_frequency"]["lambda"]),
        )
        pair_count_by_id = dict(zip(posted_ids, pair_counts, strict=True))

        account_rng = self.generator.rng_for(
            "distribution:finance:ledger_entries:accounts"
        )
        attribute_rng = self.generator.rng_for(
            "distribution:finance:ledger_entries:attributes"
        )
        date_rng = self.generator.rng_for("distribution:finance:ledger_entries:dates")
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
        lag = self.generation_rules["ledger_entries"]["created_lag_hours"]

        rows: list[dict[str, Any]] = []
        entry_id = 1
        for transaction_id in posted_ids:
            transaction = transaction_lookup.loc[transaction_id]
            currency = transaction["source_currency"]
            account_pool = account_ids_by_currency[currency]
            allocations = allocate_money(
                transaction["total_amount"], pair_count_by_id[transaction_id]
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
                    seconds=int(
                        date_rng.integers(
                            int(lag["minimum"]) * 60 * 60,
                            (int(lag["maximum"]) * 60 * 60) + 1,
                        )
                    )
                )
                lines = [
                    (debit_account, format_money(allocation), ""),
                    (credit_account, "", format_money(allocation)),
                ]
                if bool(transaction["reversed"]):
                    lines = [
                        (debit_account, "", format_money(allocation)),
                        (credit_account, format_money(allocation), ""),
                    ]
                for account_id, debit_amount, credit_amount in lines:
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


def _exact_poisson_pair_counts(
    rng: Any,
    item_count: int,
    total_pairs: int,
    lam: float,
) -> list[int]:
    """Allocate an exact posting-pair total with at least one per item."""

    if item_count <= 0:
        raise ValueError("Posted transaction count must be positive")
    if total_pairs < item_count:
        raise ValueError("Ledger target cannot cover every posted transaction")
    weights = poisson_weights(rng, item_count, lam)
    counts = 1 + rng.multinomial(total_pairs - item_count, weights)
    return [int(value) for value in counts]


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Install requirements.txt."
        ) from exc
    return pd


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = ["FinanceDistributionApplier"]
