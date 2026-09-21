"""Full relational, accounting, and FX validation for Finance datasets."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import non_empty_values
from generators.core.integrity import passed
from generators.core.schema_contract import primary_key_fields
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.decimal_policy import convert_to_reporting_currency
from generators.finance.decimal_policy import signed_ledger_amount
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.config import validate_finance_config


class FinanceRelationalValidator:
    """Report complete relational and accounting validity for Finance data."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceRelationalValidator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.finance_config = load_finance_config()
        self.rules = self.finance_config["generation_rules"]

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceRelationalValidator":
        """Create a relational validator from validated Finance config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final imperfect Finance tables and validate them."""

        tables = FinanceImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return every safely applicable Finance integrity check."""

        results = self._validate_table_contract(tables)
        if any(not result.passed for result in results):
            return results

        column_results = self._validate_columns(tables)
        results.extend(column_results)
        if any(not result.passed for result in column_results):
            return results

        results.extend(self._validate_row_counts(tables))
        results.extend(self._validate_keys(tables))
        results.extend(self._validate_required_fields(tables))
        results.extend(self._validate_foreign_keys(tables))
        results.extend(self._validate_accounts(tables))
        results.extend(self._validate_transactions(tables))
        results.extend(self._validate_ledger_entries(tables))
        results.extend(self._validate_budgets(tables))
        results.extend(self._validate_fx_rates(tables))
        results.extend(self._validate_boundary_and_chronology(tables))
        return results

    def validate_or_raise(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate tables and raise one combined error for all failures."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def _validate_table_contract(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.settings.table_order
        actual = tuple(tables)
        results = [
            _result(
                "schema.table_order",
                actual == expected,
                "all tables present in dependency order",
                f"expected {list(expected)}, got {list(actual)}",
            )
        ]
        for table_name in expected:
            results.append(
                _result(
                    f"{table_name}.present",
                    table_name in tables,
                    "table present",
                    "table missing",
                )
            )
        extra = sorted(set(tables) - set(expected))
        results.append(
            _result(
                "schema.unexpected_tables",
                not extra,
                "no unexpected tables",
                f"unexpected tables: {extra}",
            )
        )
        return results

    def _validate_columns(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            expected = [
                field["name"]
                for field in self.finance_config["tables"][table_name]["fields"]
            ]
            actual = tables[table_name].columns.tolist()
            results.append(
                _result(
                    f"{table_name}.columns",
                    actual == expected,
                    "columns match config order",
                    f"expected {expected}, got {actual}",
                )
            )
        return results

    def _validate_row_counts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        expected_counts["budgets"] += count_from_pct(
            expected_counts["budgets"],
            float(self.settings.imperfections["duplicate_pct"]),
        )
        results = [
            _result(
                f"{table_name}.row_count",
                len(tables[table_name]) == expected,
                f"final row count {expected}",
                f"expected {expected}, got {len(tables[table_name])}",
            )
            for table_name, expected in expected_counts.items()
        ]
        results.extend(
            _result(
                f"{table_name}.row_cap",
                len(tables[table_name]) <= self.settings.max_rows_per_table,
                f"within row cap {self.settings.max_rows_per_table}",
                f"{len(tables[table_name])} rows exceed cap",
            )
            for table_name in self.settings.table_order
        )
        return results

    def _validate_keys(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            config = self.finance_config["tables"][table_name]
            keys = primary_key_fields(config)
            blank_columns = [
                field for field in keys if empty_count(tables[table_name][field])
            ]
            duplicates = int(tables[table_name].duplicated(keys).sum())
            results.append(
                _result(
                    f"{table_name}.primary_key",
                    not blank_columns and duplicates == 0,
                    "primary key populated and unique",
                    f"blank columns={blank_columns}, duplicate rows={duplicates}",
                )
            )
            for field in config["fields"]:
                if field.get("key") != "unique":
                    continue
                name = field["name"]
                duplicate_count = int(tables[table_name][name].duplicated().sum())
                results.append(
                    _result(
                        f"{table_name}.{name}.unique",
                        duplicate_count == 0,
                        "unique values",
                        f"{duplicate_count} duplicate value(s)",
                    )
                )
            for fields in config.get("unique_constraints", []):
                duplicate_count = int(tables[table_name].duplicated(fields).sum())
                label = "_".join(fields)
                results.append(
                    _result(
                        f"{table_name}.{label}.unique",
                        duplicate_count == 0,
                        "composite values unique",
                        f"{duplicate_count} duplicate composite value(s)",
                    )
                )
        return results

    def _validate_required_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.finance_config["tables"][table_name]["fields"]:
                if field["nullable"]:
                    continue
                name = field["name"]
                blanks = empty_count(tables[table_name][name])
                results.append(
                    _result(
                        f"{table_name}.{name}.not_null",
                        blanks == 0,
                        "no blank values",
                        f"{blanks} blank value(s)",
                    )
                )
        return results

    def _validate_foreign_keys(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for relationship in self.finance_config["relationships"]:
            if relationship.get("relationship_type") != "foreign_key":
                continue
            parent_table = relationship["parent_table"]
            parent_field = relationship["parent_field"]
            child_table = relationship["child_table"]
            child_field = relationship["child_field"]
            allowed = non_empty_values(tables[parent_table][parent_field])
            actual = non_empty_values(tables[child_table][child_field])
            invalid = sorted(actual - allowed)
            results.append(
                _result(
                    f"{child_table}.{child_field}.fk",
                    not invalid,
                    "all references valid",
                    f"invalid references: {invalid[:5]}",
                )
            )
        return results

    def _validate_accounts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        accounts = tables["accounts"]
        mappings = self.finance_config["business_mappings"]
        values = self.finance_config["domain_values"]
        invalid_types = set(accounts["account_type"]) - set(values["account_types"])
        invalid_currencies = set(accounts["currency_code"]) - set(
            values["source_currencies"]
        )
        invalid_subtypes = 0
        invalid_balances = 0
        invalid_parent_types = 0
        parent_map: dict[int, int | None] = {}
        lookup = accounts.set_index("account_id")
        for row in accounts.itertuples(index=False):
            if row.account_type in mappings["account_subtypes"]:
                invalid_subtypes += (
                    row.account_subtype
                    not in mappings["account_subtypes"][row.account_type]
                )
                invalid_balances += (
                    row.normal_balance
                    != mappings["normal_balance_by_account_type"][row.account_type]
                )
            if row.parent_account_id == "":
                parent_map[int(row.account_id)] = None
            else:
                parent_id = int(row.parent_account_id)
                parent_map[int(row.account_id)] = parent_id
                if parent_id in lookup.index:
                    invalid_parent_types += (
                        lookup.at[parent_id, "account_type"] != row.account_type
                    )
        cycle_count = _hierarchy_cycle_count(parent_map)
        roots = sum(parent is None for parent in parent_map.values())
        children = len(parent_map) - roots
        return [
            _result(
                "accounts.account_type.domain",
                not invalid_types,
                "all account types configured",
                f"invalid types: {sorted(invalid_types)}",
            ),
            _result(
                "accounts.currency_code.domain",
                not invalid_currencies,
                "all account currencies configured",
                f"invalid currencies: {sorted(invalid_currencies)}",
            ),
            _count_result("accounts.account_subtype.mapping", invalid_subtypes),
            _count_result("accounts.normal_balance.mapping", invalid_balances),
            _count_result("accounts.parent_type.mapping", invalid_parent_types),
            _count_result("accounts.hierarchy_acyclic", cycle_count),
            _result(
                "accounts.hierarchy_shape",
                roots > 0 and children > 0,
                f"{roots} root(s), {children} child account(s)",
                f"roots={roots}, children={children}",
            ),
        ]

    def _validate_transactions(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        transactions = tables["transactions"]
        values = self.finance_config["domain_values"]
        reporting = self.finance_config["business_mappings"]["reporting_currency"]
        invalid_sources = set(transactions["source_system"]) - set(
            values["source_systems"]
        )
        invalid_currencies = set(transactions["source_currency"]) - set(
            values["source_currencies"]
        )
        target_currencies = set(transactions["target_currency"])
        amount_scale = 0
        non_positive = 0
        for value in transactions["total_amount"]:
            amount = Decimal(str(value))
            amount_scale += amount.as_tuple().exponent != -4
            non_positive += amount <= 0
        return [
            _result(
                "transactions.source_system.domain",
                not invalid_sources,
                "all source systems configured",
                f"invalid systems: {sorted(invalid_sources)}",
            ),
            _result(
                "transactions.source_currency.domain",
                not invalid_currencies,
                "all source currencies configured",
                f"invalid currencies: {sorted(invalid_currencies)}",
            ),
            _result(
                "transactions.target_currency",
                target_currencies == {reporting},
                f"all target values use {reporting}",
                f"expected {reporting}, got {sorted(target_currencies)}",
            ),
            _count_result("transactions.total_amount.scale", amount_scale),
            _count_result("transactions.total_amount.positive", non_positive),
        ]

    def _validate_ledger_entries(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        ledger = tables["ledger_entries"]
        transactions = tables["transactions"].set_index("transaction_id")
        accounts = tables["accounts"].set_index("account_id")
        posting_types = set(self.finance_config["domain_values"]["posting_types"])
        exclusivity = 0
        positivity = 0
        scale = 0
        currency = 0
        account_currency = 0
        posting_type = 0
        reversal = 0
        creation = 0
        signed_amount_errors = 0
        for row in ledger.itertuples(index=False):
            debit_blank = row.debit_amount == ""
            credit_blank = row.credit_amount == ""
            exclusivity += debit_blank == credit_blank
            populated = row.credit_amount if debit_blank else row.debit_amount
            if populated != "":
                amount = Decimal(str(populated))
                positivity += amount <= 0
                scale += amount.as_tuple().exponent != -4
            if row.transaction_id in transactions.index:
                transaction = transactions.loc[row.transaction_id]
                currency += row.currency_code != transaction["source_currency"]
                reversal += bool(transaction["reversed"]) != (
                    row.posting_type == "Reversal"
                )
                creation += datetime.fromisoformat(
                    row.created_at
                ) < datetime.fromisoformat(transaction["posted_at"])
            if row.account_id in accounts.index:
                account_currency += row.currency_code != accounts.at[
                    row.account_id, "currency_code"
                ]
                try:
                    signed_ledger_amount(
                        None if debit_blank else row.debit_amount,
                        None if credit_blank else row.credit_amount,
                        accounts.at[row.account_id, "normal_balance"],
                    )
                except (TypeError, ValueError):
                    signed_amount_errors += 1
            posting_type += row.posting_type not in posting_types

        sequence = 0
        odd_lines = 0
        balance = 0
        transaction_total = 0
        for transaction_id, lines in ledger.groupby("transaction_id", sort=False):
            sequence += lines["line_number"].tolist() != list(
                range(1, len(lines) + 1)
            )
            odd_lines += len(lines) % 2 != 0
            debit_total = sum(
                Decimal(value) for value in lines["debit_amount"] if value != ""
            )
            credit_total = sum(
                Decimal(value) for value in lines["credit_amount"] if value != ""
            )
            balance += debit_total != credit_total
            if transaction_id in transactions.index:
                transaction_total += debit_total != Decimal(
                    str(transactions.at[transaction_id, "total_amount"])
                )

        posted_ids = set(ledger["transaction_id"])
        unmatched = len(set(transactions.index) - posted_ids)
        expected_unmatched = round(
            len(transactions)
            * float(self.rules["transactions"]["unposted_to_ledger_fraction"])
        )
        accounts_per_transaction = ledger.groupby("transaction_id")[
            "account_id"
        ].nunique()
        transactions_per_account = ledger.groupby("account_id")[
            "transaction_id"
        ].nunique()
        many_to_many = (
            not accounts_per_transaction.empty
            and not transactions_per_account.empty
            and int(accounts_per_transaction.max()) >= 2
            and int(transactions_per_account.max()) >= 2
        )
        return [
            _count_result("ledger_entries.debit_credit_exclusivity", exclusivity),
            _count_result("ledger_entries.populated_amount_positive", positivity),
            _count_result("ledger_entries.amount_scale", scale),
            _count_result("ledger_entries.transaction_currency", currency),
            _count_result("ledger_entries.account_currency", account_currency),
            _count_result("ledger_entries.posting_type.domain", posting_type),
            _count_result("ledger_entries.reversal_semantics", reversal),
            _temporal_result("ledger_entries.created_at_temporal", creation),
            _count_result("ledger_entries.signed_amount", signed_amount_errors),
            _count_result("ledger_entries.line_sequence", sequence),
            _count_result("ledger_entries.even_line_count", odd_lines),
            _count_result("ledger_entries.transaction_balance", balance),
            _count_result(
                "ledger_entries.transaction_amount_alignment", transaction_total
            ),
            _result(
                "transactions.without_ledger",
                unmatched == expected_unmatched,
                f"{unmatched} transaction(s) intentionally unposted",
                f"expected {expected_unmatched}, got {unmatched}",
            ),
            _result(
                "finance.transaction_account_many_to_many",
                many_to_many,
                "transactions and accounts form a many-to-many ledger path",
                "ledger path lacks required many-to-many cardinality",
            ),
        ]

    def _validate_budgets(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        budgets = tables["budgets"]
        reporting = self.finance_config["business_mappings"]["reporting_currency"]
        scenarios = set(self.finance_config["domain_values"]["budget_scenarios"])
        business_keys = self.finance_config["imperfection_targets"][
            "near_duplicate_budgets"
        ]["business_key_fields"]
        expected_duplicates = count_from_pct(
            self.generator.row_count("budgets"),
            float(self.settings.imperfections["duplicate_pct"]),
        )
        duplicate_count = int(budgets.duplicated(business_keys).sum())
        invalid_periods = 0
        invalid_fiscal_year = 0
        invalid_creation = 0
        invalid_scale = 0
        non_positive = 0
        unique_periods = budgets.drop_duplicates(business_keys)
        for row in unique_periods.itertuples(index=False):
            start = date.fromisoformat(row.period_start)
            end = date.fromisoformat(row.period_end)
            invalid_periods += start >= end
            invalid_fiscal_year += int(row.fiscal_year) != start.year
            invalid_creation += datetime.fromisoformat(row.created_at).date() >= start
            amount = Decimal(str(row.budget_amount))
            invalid_scale += amount.as_tuple().exponent != -4
            non_positive += amount <= 0

        dates_by_account: dict[int, list[str]] = defaultdict(list)
        transaction_dates = dict(
            tables["transactions"][["transaction_id", "transaction_date"]].itertuples(
                index=False, name=None
            )
        )
        for account_id, transaction_id in tables["ledger_entries"][
            ["account_id", "transaction_id"]
        ].itertuples(index=False, name=None):
            if transaction_id in transaction_dates:
                dates_by_account[int(account_id)].append(
                    transaction_dates[transaction_id]
                )
        matched = 0
        unmatched = 0
        for row in unique_periods.itertuples(index=False):
            has_actual = any(
                row.period_start <= transaction_date < row.period_end
                for transaction_date in dates_by_account[int(row.account_id)]
            )
            matched += has_actual
            unmatched += not has_actual
        return [
            _result(
                "budgets.currency_code",
                set(budgets["currency_code"]) == {reporting},
                f"all budgets use {reporting}",
                f"unexpected currencies: {sorted(set(budgets['currency_code']))}",
            ),
            _result(
                "budgets.scenario.domain",
                set(budgets["scenario"]).issubset(scenarios),
                "all budget scenarios configured",
                f"invalid scenarios: {sorted(set(budgets['scenario']) - scenarios)}",
            ),
            _result(
                "budgets.controlled_duplicates",
                duplicate_count == expected_duplicates,
                f"controlled duplicate count {expected_duplicates}",
                f"expected {expected_duplicates}, got {duplicate_count}",
            ),
            _count_result("budgets.period_validity", invalid_periods),
            _count_result("budgets.fiscal_year", invalid_fiscal_year),
            _temporal_result("budgets.created_at_temporal", invalid_creation),
            _count_result("budgets.amount_scale", invalid_scale),
            _count_result("budgets.amount_positive", non_positive),
            _result(
                "budgets.with_actuals",
                matched > 0,
                f"{matched} budget period(s) have actual activity",
                "no budget period has actual activity",
            ),
            _result(
                "budgets.without_actuals",
                unmatched > 0,
                f"{unmatched} budget period(s) have no actual activity",
                "no unmatched budget period remains",
            ),
        ]

    def _validate_fx_rates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        fx_rates = tables["fx_rates"]
        transactions = tables["transactions"]
        configured_pairs = set(self.rules["fx_calendar"]["currency_pairs"])
        actual_pairs = {
            f"{row.from_currency}/{row.to_currency}"
            for row in fx_rates.itertuples(index=False)
        }
        expected_dates = int(
            self.rules["fx_calendar"]["expected_total_dates"][self.settings.profile]
        )
        pair_counts = fx_rates.groupby(["from_currency", "to_currency"]).size()
        complete_grid = (
            actual_pairs == configured_pairs
            and not pair_counts.empty
            and bool(pair_counts.eq(expected_dates).all())
        )
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
        missing = fx_rates[fx_rates["rate"].astype(str).eq("")]
        expected_missing = count_from_pct(
            self.generator.row_count("fx_rates"),
            float(self.settings.imperfections["null_pct"]),
        )
        identity = fx_rates[fx_rates["from_currency"] == "USD"]
        boundaries = set(self.settings.imperfections["boundary_dates"])
        protected_missing = int(
            (
                missing["from_currency"].eq("USD")
                | missing["rate_date"].isin(boundaries)
            ).sum()
        )
        populated = fx_rates[~fx_rates["rate"].astype(str).eq("")]
        invalid_values = 0
        for value in populated["rate"]:
            rate = Decimal(str(value))
            invalid_values += rate <= 0 or rate.as_tuple().exponent != -6

        rate_lookup = fx_rates.set_index(
            ["from_currency", "to_currency", "rate_date"]
        )["rate"]
        conversion_errors = 0
        examples = 0
        for row in transactions.itertuples(index=False):
            rate = rate_lookup.loc[
                (row.source_currency, row.target_currency, row.transaction_date)
            ]
            if rate == "":
                continue
            try:
                converted = convert_to_reporting_currency(row.total_amount, rate)
                conversion_errors += (
                    converted <= 0 or converted.as_tuple().exponent != -4
                )
            except (TypeError, ValueError):
                conversion_errors += 1
            examples += 1
            if examples == 10:
                break
        missing_keys = set(
            missing[["from_currency", "to_currency", "rate_date"]].itertuples(
                index=False, name=None
            )
        )
        return [
            _result(
                "fx_rates.grid",
                complete_grid,
                "complete configured currency/date grid",
                "currency/date grid is incomplete",
            ),
            _result(
                "fx_rates.transaction_lookup_coverage",
                not transaction_keys - fx_keys,
                "every transaction has one FX lookup row",
                f"missing keys: {list(transaction_keys - fx_keys)[:5]}",
            ),
            _result(
                "fx_rates.identity",
                len(identity) == expected_dates
                and set(identity["rate"]) == {"1.000000"},
                "USD/USD identity rates exact",
                "USD/USD identity rates missing or incorrect",
            ),
            _result(
                "fx_rates.null_count",
                len(missing) == expected_missing,
                f"controlled NULL count {expected_missing}",
                f"expected {expected_missing}, got {len(missing)}",
            ),
            _count_result("fx_rates.protected_nulls", protected_missing),
            _count_result("fx_rates.populated_values", invalid_values),
            _result(
                "fx_rates.missing_rate_path",
                bool(missing_keys & transaction_keys),
                "at least one transaction references a missing FX rate",
                "missing FX rates are not exercised by transactions",
            ),
            _result(
                "finance.fx_conversion_examples",
                examples > 0 and conversion_errors == 0,
                f"{examples} Decimal conversion example(s) valid",
                f"examples={examples}, errors={conversion_errors}",
            ),
        ]

    def _validate_boundary_and_chronology(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        transactions = tables["transactions"]
        expected_boundaries = set(self.settings.imperfections["boundary_dates"])
        boundary_counts = transactions["transaction_date"].value_counts()
        boundary_valid = all(
            int(boundary_counts.get(value, 0)) == 1 for value in expected_boundaries
        )
        transaction_lag = self.rules["transactions"]["posted_lag_days"]
        invalid_posting = 0
        for row in transactions.itertuples(index=False):
            lag = (
                datetime.fromisoformat(row.posted_at).date()
                - date.fromisoformat(row.transaction_date)
            ).days
            invalid_posting += not (
                int(transaction_lag["minimum"])
                <= lag
                <= int(transaction_lag["maximum"])
            )

        ledger_lag = self.rules["ledger_entries"]["created_lag_hours"]
        minimum = timedelta(hours=int(ledger_lag["minimum"]))
        maximum = timedelta(hours=int(ledger_lag["maximum"]))
        posted = dict(
            transactions[["transaction_id", "posted_at"]].itertuples(
                index=False, name=None
            )
        )
        invalid_ledger = 0
        for row in tables["ledger_entries"].itertuples(index=False):
            if row.transaction_id not in posted:
                continue
            lag = datetime.fromisoformat(row.created_at) - datetime.fromisoformat(
                posted[row.transaction_id]
            )
            invalid_ledger += lag < minimum or lag > maximum
        return [
            _result(
                "transactions.boundary_dates",
                boundary_valid,
                "every boundary date appears exactly once",
                "a boundary date is missing or repeated",
            ),
            _temporal_result("transactions.posted_at_temporal", invalid_posting),
            _temporal_result("ledger_entries.lag_window", invalid_ledger),
        ]


def _hierarchy_cycle_count(parent_map: dict[int, int | None]) -> int:
    cycles = 0
    for account_id in parent_map:
        visited: set[int] = set()
        current: int | None = account_id
        while current is not None and current in parent_map:
            if current in visited:
                cycles += 1
                break
            visited.add(current)
            current = parent_map[current]
    return cycles


def _result(
    check_name: str,
    condition: bool,
    passed_message: str,
    failed_message: str,
) -> IntegrityCheckResult:
    return passed(check_name, passed_message) if condition else failed(
        check_name, failed_message
    )


def _count_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "no violations",
        f"{violation_count} violation(s)",
    )


def _temporal_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "chronology valid",
        f"{violation_count} temporal violation(s)",
    )


__all__ = ["FinanceRelationalValidator"]
