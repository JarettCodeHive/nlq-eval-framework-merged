"""Controlled imperfection injection for distributed Finance data."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.progress import ProgressReporter
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.decimal_policy import allocate_money
from generators.finance.decimal_policy import format_money
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.distributed_tables import (
    validate_finance_distributed_tables,
)
from generators.finance.validators.imperfect_tables import (
    validate_finance_imperfect_tables,
)


class FinanceImperfectionInjector:
    """Inject deterministic Finance defects while preserving accounting.

    The stage starts from validated distributed tables and introduces the four
    approved Finance imperfection classes:

    - near-duplicate budget rows with fresh primary keys;
    - scale-4 transaction amount outliers with rebalanced ledger entries;
    - configured boundary transaction dates with coordinated timestamps; and
    - missing non-identity FX rates outside protected boundary dates.

    Physical foreign keys, composite FX lookup rows, account hierarchy, ledger
    row counts, and posted/unposted transaction membership remain unchanged.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceImperfectionInjector only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.finance_config = load_finance_config()
        self.generation_rules = self.finance_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "FinanceImperfectionInjector":
        """Create an injector from validated Finance profile config."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_imperfect_tables(self) -> dict[str, Any]:
        """Regenerate distributed Finance tables and inject imperfections."""

        distributed = FinanceDistributionApplier(
            self.generator,
            progress=self.progress,
        ).generate_distributed_tables()
        return self.apply_to_tables(distributed)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return imperfect deep copies of valid distributed Finance tables."""

        self._report("Injecting controlled Finance imperfections")
        validate_finance_distributed_tables(self.generator, tables)
        imperfect = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        imperfect["budgets"] = self._inject_near_duplicate_budgets(
            imperfect["budgets"]
        )
        self._report("Injected near-duplicate budgets")

        outlier_ids = self._inject_transaction_amount_outliers(imperfect)
        self._report(
            f"Injected {len(outlier_ids)} transaction-amount outliers and "
            "rebalanced ledger entries"
        )

        boundary_ids = self._inject_transaction_boundary_dates(imperfect)
        self._report(
            f"Injected {len(boundary_ids)} boundary-date transactions and "
            "coordinated timestamps"
        )

        null_count = self._inject_missing_fx_rates(imperfect)
        self._report(f"Injected {null_count} missing FX rates")
        self._report("Validating imperfect Finance tables")
        validate_finance_imperfect_tables(
            self.generator,
            imperfect,
            source_tables=tables,
        )
        self._report("Finance imperfection injection complete")
        return imperfect

    def _inject_near_duplicate_budgets(self, budgets: Any) -> Any:
        """Append meaningful budget near-duplicates with sequential new IDs."""

        duplicate_count = count_from_pct(
            len(budgets), float(self.config["duplicate_pct"])
        )
        result = budgets.copy(deep=True)
        if duplicate_count == 0:
            return result

        rng = self.generator.rng_for("imperfections:finance:budgets:duplicates")
        sampled_positions = rng.choice(
            budgets.index.to_numpy(),
            size=duplicate_count,
            replace=False,
        )
        duplicates = (
            budgets.loc[sampled_positions].copy(deep=True).reset_index(drop=True)
        )
        start_id = int(budgets["budget_id"].max()) + 1
        duplicates["budget_id"] = range(start_id, start_id + duplicate_count)

        maximum = Decimal(
            str(self.settings.distributions["budget_amount"]["max_amount"])
        )
        unit = Decimal("0.0001")
        duplicates["budget_amount"] = [
            format_money(
                Decimal(str(value)) - unit
                if Decimal(str(value)) >= maximum
                else Decimal(str(value)) + unit
            )
            for value in duplicates["budget_amount"]
        ]
        duplicates["created_at"] = [
            _near_duplicate_timestamp(created_at, period_start)
            for created_at, period_start in zip(
                duplicates["created_at"],
                duplicates["period_start"],
                strict=True,
            )
        ]

        pd = _require_pandas()
        return pd.concat([result, duplicates], ignore_index=True)

    def _inject_transaction_amount_outliers(
        self,
        tables: dict[str, Any],
    ) -> set[int]:
        """Replace posted transaction amounts and rebalance their ledger rows."""

        transactions = tables["transactions"]
        target = self.finance_config["imperfection_targets"][
            "transaction_amount_outliers"
        ]
        outlier_count = count_from_pct(
            len(transactions), float(self.config["outlier_pct"])
        )
        if outlier_count == 0:
            return set()

        posted_ids = set(int(value) for value in tables["ledger_entries"]["transaction_id"])
        eligible_positions = transactions.index[
            transactions["transaction_id"].isin(posted_ids)
        ].to_numpy()
        if outlier_count > len(eligible_positions):
            raise ValueError("Finance transaction outlier target exceeds posted capacity")

        rng = self.generator.rng_for(
            "imperfections:finance:transactions:amount_outliers"
        )
        selected_positions = rng.choice(
            eligible_positions,
            size=outlier_count,
            replace=False,
        )
        scale = int(target["scale"])
        multiplier = 10**scale
        amount_units = rng.integers(
            int(target["minimum_value"]) * multiplier,
            (int(target["maximum_value"]) * multiplier) + 1,
            size=outlier_count,
        )
        transactions.loc[selected_positions, "total_amount"] = [
            format_money(Decimal(int(value)).scaleb(-scale)) for value in amount_units
        ]
        transaction_ids = {
            int(value)
            for value in transactions.loc[selected_positions, "transaction_id"]
        }
        _reallocate_ledger_amounts(tables, transaction_ids)
        return transaction_ids

    def _inject_transaction_boundary_dates(
        self,
        tables: dict[str, Any],
    ) -> set[int]:
        """Inject every boundary date and shift dependent timestamps safely."""

        transactions = tables["transactions"]
        boundaries = list(self.config["boundary_dates"])
        if not boundaries:
            return set()
        posted_ids = set(int(value) for value in tables["ledger_entries"]["transaction_id"])
        eligible_positions = transactions.index[
            transactions["transaction_id"].isin(posted_ids)
        ].to_numpy()
        if len(boundaries) > len(eligible_positions):
            raise ValueError("Finance boundary target exceeds posted capacity")

        rng = self.generator.rng_for(
            "imperfections:finance:transactions:boundary_dates"
        )
        selected_positions = rng.choice(
            eligible_positions,
            size=len(boundaries),
            replace=False,
        )
        posted_lag = self.generation_rules["transactions"]["posted_lag_days"]
        selected_ids: set[int] = set()
        for position, boundary in zip(selected_positions, boundaries, strict=True):
            transaction_id = int(transactions.at[position, "transaction_id"])
            old_posted = datetime.fromisoformat(transactions.at[position, "posted_at"])
            day_lag = int(
                rng.integers(
                    int(posted_lag["minimum"]),
                    int(posted_lag["maximum"]) + 1,
                )
            )
            second = int(rng.integers(0, 24 * 60 * 60))
            new_posted = datetime.combine(
                date.fromisoformat(boundary) + timedelta(days=day_lag),
                time.min,
            ) + timedelta(seconds=second)
            transactions.at[position, "transaction_date"] = boundary
            transactions.at[position, "posted_at"] = _format_timestamp(new_posted)
            _shift_ledger_timestamps(
                tables["ledger_entries"],
                transaction_id,
                old_posted,
                new_posted,
            )
            selected_ids.add(transaction_id)
        return selected_ids

    def _inject_missing_fx_rates(self, tables: dict[str, Any]) -> int:
        """Null queryable FX rates while protecting identity and boundary rows."""

        fx_rates = tables["fx_rates"]
        null_count = count_from_pct(len(fx_rates), float(self.config["null_pct"]))
        if null_count == 0:
            return 0

        target = self.finance_config["imperfection_targets"]["missing_fx_rates"]
        boundaries = set(self.config["boundary_dates"])
        protected_pair = str(target["protected_pair"])
        from_currency, to_currency = protected_pair.split("/", 1)
        eligible = fx_rates[
            ~(
                (fx_rates["from_currency"] == from_currency)
                & (fx_rates["to_currency"] == to_currency)
            )
            & ~fx_rates["rate_date"].isin(boundaries)
        ]
        if null_count > len(eligible):
            raise ValueError("Finance FX NULL target exceeds eligible capacity")

        used_keys = set(
            tables["transactions"][
                ["source_currency", "target_currency", "transaction_date"]
            ].itertuples(index=False, name=None)
        )
        used_positions = [
            int(row.Index)
            for row in eligible.itertuples()
            if (row.from_currency, row.to_currency, row.rate_date) in used_keys
        ]
        unused_positions = [
            int(position)
            for position in eligible.index
            if int(position) not in set(used_positions)
        ]
        rng = self.generator.rng_for("imperfections:finance:fx_rates:nulls")
        selected = _sample_positions(
            rng,
            preferred=used_positions,
            fallback=unused_positions,
            count=null_count,
        )
        fx_rates["rate"] = fx_rates["rate"].astype("object")
        fx_rates.loc[selected, "rate"] = ""
        return len(selected)

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _reallocate_ledger_amounts(
    tables: dict[str, Any],
    transaction_ids: set[int],
) -> None:
    """Make each selected transaction's debit and credit sides exact again."""

    transactions = tables["transactions"].set_index("transaction_id")
    ledger = tables["ledger_entries"]
    for transaction_id in sorted(transaction_ids):
        positions = ledger.index[ledger["transaction_id"] == transaction_id]
        debit_positions = [
            int(position)
            for position in positions
            if ledger.at[position, "debit_amount"] != ""
        ]
        credit_positions = [
            int(position)
            for position in positions
            if ledger.at[position, "credit_amount"] != ""
        ]
        amount = transactions.at[transaction_id, "total_amount"]
        debit_allocations = allocate_money(amount, len(debit_positions))
        credit_allocations = allocate_money(amount, len(credit_positions))
        ledger.loc[debit_positions, "debit_amount"] = [
            format_money(value) for value in debit_allocations
        ]
        ledger.loc[credit_positions, "credit_amount"] = [
            format_money(value) for value in credit_allocations
        ]


def _shift_ledger_timestamps(
    ledger: Any,
    transaction_id: int,
    old_posted: datetime,
    new_posted: datetime,
) -> None:
    """Preserve each ledger line's posting lag after a boundary-date move."""

    positions = ledger.index[ledger["transaction_id"] == transaction_id]
    for position in positions:
        old_created = datetime.fromisoformat(ledger.at[position, "created_at"])
        ledger.at[position, "created_at"] = _format_timestamp(
            new_posted + (old_created - old_posted)
        )


def _near_duplicate_timestamp(value: str, period_start: str) -> str:
    """Move a budget timestamp by one second while retaining chronology."""

    current = datetime.fromisoformat(value)
    period = datetime.combine(date.fromisoformat(period_start), time.min)
    changed = current + timedelta(seconds=1)
    if changed >= period:
        changed = current - timedelta(seconds=1)
    return _format_timestamp(changed)


def _sample_positions(
    rng: Any,
    preferred: list[int],
    fallback: list[int],
    count: int,
) -> list[int]:
    """Sample preferred positions first, then fill from fallback positions."""

    preferred_count = min(count, len(preferred))
    selected = (
        [
            int(value)
            for value in rng.choice(
                preferred,
                size=preferred_count,
                replace=False,
            )
        ]
        if preferred_count
        else []
    )
    remaining = count - preferred_count
    if remaining:
        selected.extend(
            int(value)
            for value in rng.choice(fallback, size=remaining, replace=False)
        )
    return selected


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for Finance imperfections") from exc
    return pd


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = ["FinanceImperfectionInjector"]
