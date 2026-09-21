"""Immediate validation for controlled Finance imperfection results."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import primary_key_fields
from generators.finance.config import load_finance_config
from generators.finance.validators.distributed_tables import (
    validate_finance_distributed_tables,
)


IMPERFECTION_MUTABLE_FIELDS = {
    "accounts": set(),
    "transactions": {"transaction_date", "total_amount", "posted_at"},
    "ledger_entries": {"debit_amount", "credit_amount", "created_at"},
    "budgets": set(),
    "fx_rates": {"rate"},
}


class FinanceImperfectTablesValidator:
    """Permit only approved Finance defects and reject collateral damage."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError(
                "FinanceImperfectTablesValidator only supports the finance domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.finance_config = load_finance_config()
        self.generation_rules = self.finance_config["generation_rules"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate structure, defect counts, accounting, and source drift."""

        if source_tables is not None:
            validate_finance_distributed_tables(self.generator, source_tables)
        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_unique_constraints(tables)
        self._validate_foreign_keys(tables)
        self._validate_accounts(tables)
        self._validate_duplicate_budgets(tables)
        self._validate_missing_fx_rates(tables)
        self._validate_transaction_outliers(tables)
        self._validate_boundary_transactions(tables)
        self._validate_ledger_entries(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Imperfect Finance table order differs from config")
        for table_name in self.settings.table_order:
            table = tables[table_name]
            expected_columns = [
                field["name"]
                for field in self.finance_config["tables"][table_name]["fields"]
            ]
            if table.columns.tolist() != expected_columns:
                raise ValueError(
                    f"{table_name} columns changed after Finance imperfections"
                )
            expected_rows = self.generator.row_count(table_name)
            if table_name == "budgets":
                expected_rows += self._expected_duplicate_count()
            if len(table) != expected_rows:
                raise ValueError(
                    f"{table_name} imperfect row count differs from expected: "
                    f"expected {expected_rows}, got {len(table)}"
                )
            if len(table) > self.settings.max_rows_per_table:
                raise ValueError(f"{table_name} exceeds the configured row cap")

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.finance_config["tables"][table_name]
            table = tables[table_name]
            primary_keys = primary_key_fields(table_config)
            if any(_blank_count(table[field]) for field in primary_keys):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(primary_keys).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                name = field["name"]
                if not field["nullable"] and _blank_count(table[name]):
                    raise ValueError(
                        f"Required field contains blanks: {table_name}.{name}"
                    )
                if field.get("key") == "unique" and table[name].duplicated().any():
                    raise ValueError(f"{table_name}.{name} contains duplicate values")

    def _validate_unique_constraints(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.finance_config["tables"].items():
            for fields in table_config.get("unique_constraints", []):
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

        fx_keys = set(
            tables["fx_rates"]
            .loc[:, ["from_currency", "to_currency", "rate_date"]]
            .itertuples(index=False, name=None)
        )
        transaction_keys = set(
            tables["transactions"]
            .loc[:, ["source_currency", "target_currency", "transaction_date"]]
            .itertuples(index=False, name=None)
        )
        if transaction_keys - fx_keys:
            raise ValueError("Finance transaction contains an FX-key orphan")

    def _validate_accounts(self, tables: dict[str, Any]) -> None:
        accounts = tables["accounts"]
        mappings = self.finance_config["business_mappings"]
        parent_map: dict[int, int | None] = {}
        account_lookup = accounts.set_index("account_id")
        for row in accounts.itertuples(index=False):
            if row.account_subtype not in mappings["account_subtypes"][row.account_type]:
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
                if account_lookup.at[parent_id, "account_type"] != row.account_type:
                    raise ValueError("Finance parent and child account types differ")
                parent_map[int(row.account_id)] = parent_id
        _assert_acyclic(parent_map)

    def _validate_duplicate_budgets(self, tables: dict[str, Any]) -> None:
        budgets = tables["budgets"]
        base_count = self.generator.row_count("budgets")
        expected = self._expected_duplicate_count()
        base_rows = budgets.iloc[:base_count]
        duplicate_rows = budgets.iloc[base_count:]
        if len(duplicate_rows) != expected:
            raise ValueError("Near-duplicate budget count differs from config")

        target = self.finance_config["imperfection_targets"][
            "near_duplicate_budgets"
        ]
        business_keys = target["business_key_fields"]
        variation_fields = set(target["variation_fields"])
        if base_rows.duplicated(business_keys).any():
            raise ValueError("Base budget business keys are not unique")
        expected_ids = list(
            range(
                int(base_rows["budget_id"].max()) + 1,
                int(base_rows["budget_id"].max()) + expected + 1,
            )
        )
        if duplicate_rows["budget_id"].tolist() != expected_ids:
            raise ValueError("Near-duplicate budget IDs are not fresh and sequential")

        source_by_key = base_rows.set_index(business_keys, drop=False)
        unchanged_fields = (
            set(budgets.columns) - variation_fields - {"budget_id"}
        )
        for row in duplicate_rows.itertuples(index=False):
            key = tuple(getattr(row, field) for field in business_keys)
            lookup_key: Any = key[0] if len(key) == 1 else key
            if lookup_key not in source_by_key.index:
                raise ValueError("Near-duplicate budget has no source business key")
            source = source_by_key.loc[lookup_key]
            if any(getattr(row, field) != source[field] for field in unchanged_fields):
                raise ValueError(
                    "Near-duplicate budget changed a non-variation field"
                )
            if not any(
                getattr(row, field) != source[field] for field in variation_fields
            ):
                raise ValueError("Near-duplicate budget is byte-identical")
            if datetime.fromisoformat(row.created_at).date() >= date.fromisoformat(
                row.period_start
            ):
                raise ValueError("Near-duplicate budget creation is not before period")
            _require_decimal_scale(row.budget_amount, 4, "budgets.budget_amount")

        if int(budgets.duplicated(business_keys).sum()) != expected:
            raise ValueError("Budget business-key duplicate count differs from config")

    def _validate_missing_fx_rates(self, tables: dict[str, Any]) -> None:
        fx_rates = tables["fx_rates"]
        expected = count_from_pct(
            self.generator.row_count("fx_rates"),
            float(self.config["null_pct"]),
        )
        missing = fx_rates[fx_rates["rate"].map(_is_blank)]
        if len(missing) != expected:
            raise ValueError(
                "fx_rates.rate NULL count differs from configured rate: "
                f"expected {expected}, got {len(missing)}"
            )

        target = self.finance_config["imperfection_targets"]["missing_fx_rates"]
        protected_from, protected_to = str(target["protected_pair"]).split("/", 1)
        if (
            (missing["from_currency"] == protected_from)
            & (missing["to_currency"] == protected_to)
        ).any():
            raise ValueError("Finance identity FX rows contain missing rates")
        if missing["rate_date"].isin(self.config["boundary_dates"]).any():
            raise ValueError("Finance boundary FX rows contain missing rates")

        populated = fx_rates[~fx_rates["rate"].map(_is_blank)]
        for value in populated["rate"]:
            rate = _require_decimal_scale(value, 6, "fx_rates.rate")
            if rate <= 0:
                raise ValueError("Finance populated FX rates must be positive")
        identity = fx_rates[
            (fx_rates["from_currency"] == protected_from)
            & (fx_rates["to_currency"] == protected_to)
        ]
        if set(identity["rate"]) != {"1.000000"}:
            raise ValueError("Finance USD/USD identity rates differ from 1.000000")

        missing_keys = set(
            missing[["from_currency", "to_currency", "rate_date"]].itertuples(
                index=False, name=None
            )
        )
        transaction_keys = set(
            tables["transactions"][
                ["source_currency", "target_currency", "transaction_date"]
            ].itertuples(index=False, name=None)
        )
        if not missing_keys & transaction_keys:
            raise ValueError("Missing FX rates are not exercised by transactions")

    def _validate_transaction_outliers(self, tables: dict[str, Any]) -> None:
        transactions = tables["transactions"]
        target = self.finance_config["imperfection_targets"][
            "transaction_amount_outliers"
        ]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        base_maximum = Decimal(
            str(self.settings.distributions["transaction_amount"]["max_amount"])
        )
        expected = count_from_pct(
            self.generator.row_count("transactions"),
            float(self.config["outlier_pct"]),
        )
        outlier_ids: set[int] = set()
        for row in transactions.itertuples(index=False):
            amount = _require_decimal_scale(
                row.total_amount,
                int(target["scale"]),
                "transactions.total_amount",
            )
            if amount >= minimum:
                if amount > maximum:
                    raise ValueError("A transaction outlier exceeds its maximum")
                outlier_ids.add(int(row.transaction_id))
            elif amount > base_maximum:
                raise ValueError("A transaction amount falls between normal and outlier ranges")
        if len(outlier_ids) != expected:
            raise ValueError(
                "Transaction outlier count differs from configured rate: "
                f"expected {expected}, got {len(outlier_ids)}"
            )
        posted_ids = set(int(value) for value in tables["ledger_entries"]["transaction_id"])
        if not outlier_ids.issubset(posted_ids):
            raise ValueError("A transaction outlier has no ledger entries")

    def _validate_boundary_transactions(self, tables: dict[str, Any]) -> None:
        transactions = tables["transactions"]
        fx_rates = tables["fx_rates"].set_index(
            ["from_currency", "to_currency", "rate_date"]
        )
        expected = list(self.config["boundary_dates"])
        boundary_rows = transactions[transactions["transaction_date"].isin(expected)]
        counts = boundary_rows["transaction_date"].value_counts().to_dict()
        if any(counts.get(value, 0) != 1 for value in expected):
            raise ValueError("Finance transaction boundary dates are missing or repeated")

        lag_rules = self.generation_rules["transactions"]["posted_lag_days"]
        maximum_lag = int(lag_rules["maximum"])
        posted_ids = set(int(value) for value in tables["ledger_entries"]["transaction_id"])
        for row in boundary_rows.itertuples(index=False):
            if int(row.transaction_id) not in posted_ids:
                raise ValueError("A boundary transaction has no ledger entries")
            transaction_date = date.fromisoformat(row.transaction_date)
            posted = datetime.fromisoformat(row.posted_at)
            lag = (posted.date() - transaction_date).days
            if lag < int(lag_rules["minimum"]) or lag > maximum_lag:
                raise ValueError("Boundary transaction posted_at is outside lag rules")
            rate = fx_rates.at[
                (row.source_currency, row.target_currency, row.transaction_date),
                "rate",
            ]
            if _is_blank(rate):
                raise ValueError("Boundary transaction has a missing FX rate")

    def _validate_ledger_entries(self, tables: dict[str, Any]) -> None:
        ledger = tables["ledger_entries"]
        transactions = tables["transactions"].set_index("transaction_id")
        posting_types = set(self.finance_config["domain_values"]["posting_types"])
        lag_rules = self.generation_rules["ledger_entries"]["created_lag_hours"]
        minimum_lag = timedelta(hours=int(lag_rules["minimum"]))
        maximum_lag = timedelta(hours=int(lag_rules["maximum"]))
        for row in ledger.itertuples(index=False):
            debit_blank = _is_blank(row.debit_amount)
            credit_blank = _is_blank(row.credit_amount)
            if debit_blank == credit_blank:
                raise ValueError(
                    "Finance ledger row must populate exactly one debit or credit"
                )
            populated = row.credit_amount if debit_blank else row.debit_amount
            amount = _require_decimal_scale(
                populated, 4, "ledger_entries populated amount"
            )
            if amount <= 0:
                raise ValueError("Finance populated ledger amount must be positive")
            transaction = transactions.loc[row.transaction_id]
            if row.currency_code != transaction["source_currency"]:
                raise ValueError("Finance ledger currency differs from transaction")
            if row.posting_type not in posting_types or (
                bool(transaction["reversed"]) != (row.posting_type == "Reversal")
            ):
                raise ValueError("Finance ledger posting type is invalid")
            created_lag = datetime.fromisoformat(row.created_at) - datetime.fromisoformat(
                transaction["posted_at"]
            )
            if created_lag < minimum_lag or created_lag > maximum_lag:
                raise ValueError("Finance ledger creation is outside lag rules")

        for transaction_id, lines in ledger.groupby("transaction_id", sort=False):
            if lines["line_number"].tolist() != list(range(1, len(lines) + 1)):
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
                transactions.at[transaction_id, "total_amount"]
            )
            if debit_total != credit_total or debit_total != transaction_amount:
                raise ValueError("Finance ledger totals differ from transaction amount")

        posted_ids = set(int(value) for value in ledger["transaction_id"])
        expected_unposted = round(
            len(transactions)
            * float(
                self.generation_rules["transactions"][
                    "unposted_to_ledger_fraction"
                ]
            )
        )
        if len(set(int(value) for value in transactions.index) - posted_ids) != expected_unposted:
            raise ValueError("Finance zero-ledger transaction count differs from config")
        if ledger.groupby("transaction_id")["account_id"].nunique().max() < 2:
            raise ValueError("Finance lacks a transaction using multiple accounts")
        if ledger.groupby("account_id")["transaction_id"].nunique().max() < 2:
            raise ValueError("Finance lacks an account used by multiple transactions")

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Finance table order differs from config")
        for table_name in self.settings.table_order:
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].iloc[: len(source)].reset_index(drop=True)
            if source.columns.tolist() != result.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during imperfection injection"
                )
            for column_name in source.columns:
                if column_name not in IMPERFECTION_MUTABLE_FIELDS[table_name] and not source[
                    column_name
                ].equals(result[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "imperfection injection"
                    )

        source_posted = set(source_tables["ledger_entries"]["transaction_id"])
        imperfect_posted = set(tables["ledger_entries"]["transaction_id"])
        if source_posted != imperfect_posted:
            raise ValueError(
                "Posted transaction membership changed during imperfection injection"
            )

    def _expected_duplicate_count(self) -> int:
        return count_from_pct(
            self.generator.row_count("budgets"),
            float(self.config["duplicate_pct"]),
        )


def validate_finance_imperfect_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate Finance imperfection-result guard."""

    FinanceImperfectTablesValidator(generator).validate(tables, source_tables)


def _assert_acyclic(parent_map: dict[int, int | None]) -> None:
    for account_id in parent_map:
        visited: set[int] = set()
        current: int | None = account_id
        while current is not None:
            if current in visited:
                raise ValueError("Finance account hierarchy contains a cycle")
            visited.add(current)
            current = parent_map[current]


def _require_decimal_scale(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains an invalid decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} must use scale {scale}")
    return parsed


def _blank_count(series: Any) -> int:
    return int(series.isna().sum() + series.astype(str).eq("").sum())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "FinanceImperfectTablesValidator",
    "IMPERFECTION_MUTABLE_FIELDS",
    "validate_finance_imperfect_tables",
]
