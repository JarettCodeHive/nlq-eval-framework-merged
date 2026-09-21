"""Immediate structural, accounting, and FX guards for clean Finance tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.finance.config import load_base_config
from generators.finance.config import load_finance_config


class FinanceGeneratedTablesValidator:
    """Reject invalid clean Finance tables before generation returns."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError(
                "FinanceGeneratedTablesValidator only supports the finance domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.base_config = load_base_config()
        self.finance_config = load_finance_config()

    def validate(self, tables: dict[str, Any]) -> None:
        """Validate clean table contracts and all immediate Finance invariants."""

        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_unique_constraints(tables)
        self._validate_foreign_keys(tables)
        self._validate_decimal_scales(tables)
        self._validate_accounts(tables)
        self._validate_fx_rates(tables)
        self._validate_transactions(tables)
        self._validate_ledger_entries(tables)
        self._validate_budgets(tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Generated Finance table order differs from config")
        for table_name in self.settings.table_order:
            expected_columns = [
                field["name"]
                for field in self.finance_config["tables"][table_name]["fields"]
            ]
            actual_columns = tables[table_name].columns.tolist()
            if actual_columns != expected_columns:
                raise ValueError(f"{table_name} generated columns differ from contract")
            expected_rows = self.generator.row_count(table_name)
            actual_rows = len(tables[table_name])
            if actual_rows != expected_rows:
                raise ValueError(
                    f"{table_name} generated row count differs from config: "
                    f"expected {expected_rows}, got {actual_rows}"
                )

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.finance_config["tables"][table_name]
            table = tables[table_name]
            key_fields = primary_key_fields(table_config)
            if any(_has_blanks(table[field]) for field in key_fields):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(key_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _has_blanks(table[field["name"]]):
                    raise ValueError(
                        f"{table_name}.{field['name']} contains blank required values"
                    )

    def _validate_unique_constraints(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.finance_config["tables"].items():
            unique_fields = [
                [field["name"]]
                for field in table_config["fields"]
                if field.get("key") == "unique"
            ]
            unique_fields.extend(table_config.get("unique_constraints", []))
            for fields in unique_fields:
                if tables[table_name].duplicated(fields).any():
                    raise ValueError(
                        f"{table_name} contains duplicate unique key {fields}"
                    )

    def _validate_foreign_keys(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.finance_config["tables"].items():
            for field in table_config["fields"]:
                reference = field.get("references")
                if reference is None:
                    continue
                parent_values = set(
                    tables[reference["table"]][reference["field"]].tolist()
                )
                child_values = {
                    value
                    for value in tables[table_name][field["name"]].tolist()
                    if not _is_blank(value)
                }
                if child_values - parent_values:
                    raise ValueError(
                        f"{table_name}.{field['name']} contains orphan values"
                    )

    def _validate_decimal_scales(self, tables: dict[str, Any]) -> None:
        money_fields = (
            ("transactions", "total_amount"),
            ("ledger_entries", "debit_amount"),
            ("ledger_entries", "credit_amount"),
            ("budgets", "budget_amount"),
        )
        for table_name, field_name in money_fields:
            for value in tables[table_name][field_name]:
                if not _is_blank(value):
                    _require_decimal_scale(value, 4, f"{table_name}.{field_name}")
        for value in tables["fx_rates"]["rate"]:
            if not _is_blank(value):
                _require_decimal_scale(value, 6, "fx_rates.rate")

    def _validate_accounts(self, tables: dict[str, Any]) -> None:
        accounts = tables["accounts"]
        values = self.finance_config["domain_values"]
        mappings = self.finance_config["business_mappings"]
        account_types = set(values["account_types"])
        currencies = set(values["source_currencies"])
        if not set(accounts["account_type"]).issubset(account_types):
            raise ValueError("Finance accounts contain unknown account types")
        if not set(accounts["currency_code"]).issubset(currencies):
            raise ValueError("Finance accounts contain unsupported currencies")

        account_lookup = accounts.set_index("account_id")
        parent_map: dict[int, int | None] = {}
        for row in accounts.itertuples(index=False):
            if (
                row.account_subtype
                not in mappings["account_subtypes"][row.account_type]
            ):
                raise ValueError("Finance account subtype differs from account type")
            if (
                row.normal_balance
                != mappings["normal_balance_by_account_type"][row.account_type]
            ):
                raise ValueError("Finance account normal balance differs from type")
            if _is_blank(row.parent_account_id):
                parent_map[int(row.account_id)] = None
            else:
                parent_id = int(row.parent_account_id)
                parent_map[int(row.account_id)] = parent_id
                if parent_id == int(row.account_id):
                    raise ValueError("Finance account cannot be its own parent")
                if account_lookup.loc[parent_id, "account_type"] != row.account_type:
                    raise ValueError("Finance parent and child account types differ")
        if not any(parent is None for parent in parent_map.values()):
            raise ValueError("Finance account hierarchy lacks root accounts")
        if not any(parent is not None for parent in parent_map.values()):
            raise ValueError("Finance account hierarchy lacks child accounts")
        _assert_acyclic(parent_map)

        active_counts = (
            accounts[accounts["is_active"]].groupby("currency_code").size().to_dict()
        )
        if any(active_counts.get(currency, 0) < 2 for currency in currencies):
            raise ValueError("Finance lacks active posting-account currency capacity")

    def _validate_fx_rates(self, tables: dict[str, Any]) -> None:
        fx_rates = tables["fx_rates"]
        rules = self.finance_config["generation_rules"]
        mappings = self.finance_config["business_mappings"]
        pairs = rules["fx_calendar"]["currency_pairs"]
        actual_pairs = set(
            f"{from_currency}/{to_currency}"
            for from_currency, to_currency in fx_rates[
                ["from_currency", "to_currency"]
            ].itertuples(index=False, name=None)
        )
        if actual_pairs != set(pairs):
            raise ValueError("Finance FX rows do not cover configured currency pairs")
        expected_dates = int(
            rules["fx_calendar"]["expected_total_dates"][self.settings.profile]
        )
        pair_counts = fx_rates.groupby(["from_currency", "to_currency"]).size()
        if not pair_counts.eq(expected_dates).all():
            raise ValueError("Finance FX pair/date grid is incomplete")
        date_sets = {
            tuple(sorted(group["rate_date"].tolist()))
            for _, group in fx_rates.groupby(["from_currency", "to_currency"])
        }
        if len(date_sets) != 1:
            raise ValueError("Finance FX pairs use different rate-date calendars")
        boundaries = set(self.base_config["imperfections"]["boundary_dates"])
        if not boundaries.issubset(set(fx_rates["rate_date"])):
            raise ValueError("Finance FX calendar lacks configured boundary dates")
        if _has_blanks(fx_rates["rate"]):
            raise ValueError("Clean Finance FX rates contain missing values")
        if any(Decimal(value) <= 0 for value in fx_rates["rate"]):
            raise ValueError("Clean Finance FX rates must be positive")
        if not set(fx_rates["rate_source"]).issubset(
            set(self.finance_config["domain_values"]["rate_sources"])
        ):
            raise ValueError("Finance FX rows contain unknown rate sources")
        identity = fx_rates[
            (fx_rates["from_currency"] == mappings["reporting_currency"])
            & (fx_rates["to_currency"] == mappings["reporting_currency"])
        ]
        if len(identity) != expected_dates or set(identity["rate"]) != {"1.000000"}:
            raise ValueError("Finance USD/USD identity rates differ from 1.000000")
        for row in fx_rates.itertuples(index=False):
            if datetime.fromisoformat(row.created_at).date() != date.fromisoformat(
                row.rate_date
            ):
                raise ValueError("Finance FX created_at differs from rate_date")

    def _validate_transactions(self, tables: dict[str, Any]) -> None:
        transactions = tables["transactions"]
        fx_rates = tables["fx_rates"]
        values = self.finance_config["domain_values"]
        rules = self.finance_config["generation_rules"]["transactions"]
        reporting_currency = self.finance_config["business_mappings"][
            "reporting_currency"
        ]
        if not set(transactions["source_currency"]).issubset(
            set(values["source_currencies"])
        ):
            raise ValueError("Finance transactions contain unsupported currencies")
        if set(transactions["target_currency"]) != {reporting_currency}:
            raise ValueError("Finance transaction target currency must be USD")
        if not set(transactions["source_system"]).issubset(
            set(values["source_systems"])
        ):
            raise ValueError("Finance transactions contain unknown source systems")
        if any(Decimal(value) <= 0 for value in transactions["total_amount"]):
            raise ValueError("Finance transaction amounts must be positive")

        fx_keys = set(
            fx_rates[["from_currency", "to_currency", "rate_date"]].itertuples(
                index=False, name=None
            )
        )
        transaction_keys = set(
            transactions[
                ["source_currency", "target_currency", "transaction_date"]
            ].itertuples(index=False, name=None)
        )
        if transaction_keys - fx_keys:
            raise ValueError("Finance transaction contains an FX-key orphan")
        boundaries = set(self.base_config["imperfections"]["boundary_dates"])
        if set(transactions["transaction_date"]) & boundaries:
            raise ValueError("Clean Finance transactions contain boundary dates")

        maximum_lag = int(rules["posted_lag_days"]["maximum"])
        for row in transactions.itertuples(index=False):
            transaction_date = date.fromisoformat(row.transaction_date)
            posted = datetime.fromisoformat(row.posted_at)
            lag = (posted.date() - transaction_date).days
            if lag < 0 or lag > maximum_lag:
                raise ValueError("Finance posted_at is outside transaction lag rules")

    def _validate_ledger_entries(self, tables: dict[str, Any]) -> None:
        ledger = tables["ledger_entries"]
        transactions = tables["transactions"]
        transaction_lookup = transactions.set_index("transaction_id")
        posting_types = set(self.finance_config["domain_values"]["posting_types"])
        if not set(ledger["posting_type"]).issubset(posting_types):
            raise ValueError("Finance ledger contains unknown posting types")
        posted_ids = set(int(value) for value in ledger["transaction_id"])
        expected_unposted = round(
            len(transactions)
            * float(
                self.finance_config["generation_rules"]["transactions"][
                    "unposted_to_ledger_fraction"
                ]
            )
        )
        if len(set(transactions["transaction_id"]) - posted_ids) != expected_unposted:
            raise ValueError(
                "Finance zero-ledger transaction count differs from config"
            )

        for row in ledger.itertuples(index=False):
            debit_blank = _is_blank(row.debit_amount)
            credit_blank = _is_blank(row.credit_amount)
            if debit_blank == credit_blank:
                raise ValueError(
                    "Finance ledger row must populate exactly one debit or credit"
                )
            populated = row.credit_amount if debit_blank else row.debit_amount
            if Decimal(populated) <= 0:
                raise ValueError("Finance populated ledger amount must be positive")
            transaction = transaction_lookup.loc[row.transaction_id]
            if row.currency_code != transaction["source_currency"]:
                raise ValueError("Finance ledger currency differs from transaction")
            if bool(transaction["reversed"]) != (row.posting_type == "Reversal"):
                raise ValueError(
                    "Finance ledger posting type differs from reversal semantics"
                )
            if datetime.fromisoformat(row.created_at) < datetime.fromisoformat(
                transaction["posted_at"]
            ):
                raise ValueError("Finance ledger creation precedes transaction posting")

        for transaction_id, lines in ledger.groupby("transaction_id", sort=False):
            expected_lines = list(range(1, len(lines) + 1))
            if lines["line_number"].tolist() != expected_lines:
                raise ValueError("Finance ledger line numbers are not sequential")
            if len(lines) % 2:
                raise ValueError("Finance transaction has an odd ledger line count")
            debit_total = sum(
                Decimal(value) for value in lines["debit_amount"] if value != ""
            )
            credit_total = sum(
                Decimal(value) for value in lines["credit_amount"] if value != ""
            )
            transaction_amount = Decimal(
                transaction_lookup.loc[transaction_id, "total_amount"]
            )
            if debit_total != credit_total:
                raise ValueError("Finance transaction debit and credit totals differ")
            if debit_total != transaction_amount:
                raise ValueError(
                    "Finance ledger posting sides differ from transaction amount"
                )

        if ledger.groupby("transaction_id")["account_id"].nunique().max() < 2:
            raise ValueError(
                "Finance ledger lacks a transaction using multiple accounts"
            )
        if ledger.groupby("account_id")["transaction_id"].nunique().max() < 2:
            raise ValueError(
                "Finance ledger lacks an account used by multiple transactions"
            )

    def _validate_budgets(self, tables: dict[str, Any]) -> None:
        budgets = tables["budgets"]
        rules = self.finance_config["generation_rules"]["budgets"]
        values = self.finance_config["domain_values"]
        reporting_currency = self.finance_config["business_mappings"][
            "reporting_currency"
        ]
        if set(budgets["currency_code"]) != {reporting_currency}:
            raise ValueError("Finance budget currency must be USD")
        if not set(budgets["scenario"]).issubset(set(values["budget_scenarios"])):
            raise ValueError("Finance budgets contain unknown scenarios")
        if any(Decimal(value) <= 0 for value in budgets["budget_amount"]):
            raise ValueError("Finance budget amounts must be positive")
        business_key = self.finance_config["imperfection_targets"][
            "near_duplicate_budgets"
        ]["business_key_fields"]
        if budgets.duplicated(business_key).any():
            raise ValueError("Clean Finance budgets contain duplicate business keys")
        expected_periods = int(rules["periods_per_account"][self.settings.profile])
        for account_id, periods in budgets.groupby("account_id"):
            ordered = periods.sort_values("period_start")
            if len(ordered) != expected_periods:
                raise ValueError(
                    f"Finance budget period count differs for account {account_id}"
                )
            previous_end: date | None = None
            for row in ordered.itertuples(index=False):
                start = date.fromisoformat(row.period_start)
                end = date.fromisoformat(row.period_end)
                if start >= end:
                    raise ValueError("Finance budget period is empty or reversed")
                if previous_end is not None and start < previous_end:
                    raise ValueError("Finance budget periods overlap")
                if int(row.fiscal_year) != start.year:
                    raise ValueError("Finance budget fiscal year differs from period")
                if datetime.fromisoformat(row.created_at).date() >= start:
                    raise ValueError("Finance budget creation is not before its period")
                previous_end = end

        dates_by_account: dict[int, list[str]] = defaultdict(list)
        transaction_dates = dict(
            tables["transactions"][["transaction_id", "transaction_date"]].itertuples(
                index=False, name=None
            )
        )
        for account_id, transaction_id in tables["ledger_entries"][
            [
                "account_id",
                "transaction_id",
            ]
        ].itertuples(index=False, name=None):
            dates_by_account[int(account_id)].append(transaction_dates[transaction_id])
        matched = []
        for row in budgets.itertuples(index=False):
            matched.append(
                any(
                    row.period_start <= transaction_date < row.period_end
                    for transaction_date in dates_by_account[int(row.account_id)]
                )
            )
        if not any(matched):
            raise ValueError("Finance budgets lack periods with actual activity")
        if all(matched):
            raise ValueError("Finance budgets lack periods without actual activity")


def validate_finance_generated_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
) -> None:
    """Run all immediate clean Finance table guards or raise on first failure."""

    FinanceGeneratedTablesValidator(generator).validate(tables)


def _assert_acyclic(parent_map: dict[int, int | None]) -> None:
    """Reject a cycle in a child-to-parent account mapping."""

    for account_id in parent_map:
        visited: set[int] = set()
        current: int | None = account_id
        while current is not None:
            if current in visited:
                raise ValueError("Finance account hierarchy contains a cycle")
            visited.add(current)
            current = parent_map[current]


def _require_decimal_scale(value: Any, scale: int, label: str) -> None:
    """Require a finite decimal string with exactly the configured scale."""

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains an invalid decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} must use scale {scale}")


def _has_blanks(series: Any) -> bool:
    return bool(series.isna().any() or series.eq("").any())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "FinanceGeneratedTablesValidator",
    "validate_finance_generated_tables",
]
