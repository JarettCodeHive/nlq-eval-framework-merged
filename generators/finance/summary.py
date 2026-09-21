"""Human-readable Finance generation summaries for the root CLI."""

from __future__ import annotations

from typing import Any

from generators.finance.imperfections import FinanceImperfectionInjector


def print_relationship_summary(tables: dict[str, Any]) -> None:
    """Print Finance physical, bridge, hierarchy, and FX coverage."""

    accounts = tables["accounts"]
    transactions = tables["transactions"]
    ledger_entries = tables["ledger_entries"]
    budgets = tables["budgets"]
    fx_rates = tables["fx_rates"]
    account_ids = set(accounts["account_id"])
    transaction_ids = set(transactions["transaction_id"])
    posted_transaction_ids = set(ledger_entries["transaction_id"])
    parent_ids = _non_empty_values(accounts["parent_account_id"])
    fx_keys = set(
        zip(
            fx_rates["from_currency"],
            fx_rates["to_currency"],
            fx_rates["rate_date"].astype(str),
            strict=True,
        )
    )
    transaction_fx_keys = set(
        zip(
            transactions["source_currency"],
            transactions["target_currency"],
            transactions["transaction_date"].astype(str),
            strict=True,
        )
    )
    multi_account_transactions = int(
        ledger_entries.groupby("transaction_id")["account_id"].nunique().ge(2).sum()
    )
    multi_transaction_accounts = int(
        ledger_entries.groupby("account_id")["transaction_id"].nunique().ge(2).sum()
    )

    print("relationship_checks:")
    print(f"  accounts.parent_account_id valid: {parent_ids.issubset(account_ids)}")
    print(
        "  ledger_entries.transaction_id valid: "
        f"{set(ledger_entries['transaction_id']).issubset(transaction_ids)}"
    )
    print(
        "  ledger_entries.account_id valid: "
        f"{set(ledger_entries['account_id']).issubset(account_ids)}"
    )
    print(
        "  budgets.account_id valid: "
        f"{set(budgets['account_id']).issubset(account_ids)}"
    )
    print(f"  unposted_transactions: {len(transaction_ids - posted_transaction_ids)}")
    print(f"  transactions_with_multiple_accounts: {multi_account_transactions}")
    print(f"  accounts_with_multiple_transactions: {multi_transaction_accounts}")
    print(f"  transaction_fx_keys_covered: {transaction_fx_keys.issubset(fx_keys)}")


def print_distribution_summary(tables: dict[str, Any]) -> None:
    """Print concise Finance amount, frequency, date, and FX measurements."""

    transactions = tables["transactions"]
    ledger_entries = tables["ledger_entries"]
    budgets = tables["budgets"]
    fx_rates = tables["fx_rates"]
    transaction_amounts = transactions["total_amount"].astype(float)
    budget_amounts = budgets["budget_amount"].astype(float)
    ledger_counts = ledger_entries.groupby("transaction_id").size()
    populated_rates = fx_rates.loc[
        fx_rates["rate"].astype(str).ne(""), "rate"
    ].astype(float)

    print("distribution_checks:")
    print(f"  transaction_amount_min: {transaction_amounts.min():.4f}")
    print(f"  transaction_amount_max: {transaction_amounts.max():.4f}")
    print(f"  transaction_amount_mean: {transaction_amounts.mean():.4f}")
    print(f"  ledger_lines_per_posted_transaction_mean: {ledger_counts.mean():.2f}")
    print(f"  ledger_lines_per_posted_transaction_max: {ledger_counts.max()}")
    print(f"  budget_amount_min: {budget_amounts.min():.4f}")
    print(f"  budget_amount_max: {budget_amounts.max():.4f}")
    print(f"  populated_fx_rate_min: {populated_rates.min():.6f}")
    print(f"  populated_fx_rate_max: {populated_rates.max():.6f}")
    print(f"  distinct_transaction_dates: {transactions['transaction_date'].nunique()}")


def print_imperfection_summary(
    tables: dict[str, Any],
    injector: FinanceImperfectionInjector,
) -> None:
    """Print concise Finance imperfection measurements."""

    budgets = tables["budgets"]
    transactions = tables["transactions"]
    fx_rates = tables["fx_rates"]
    duplicate_rows = len(budgets) - injector.generator.row_count("budgets")
    outlier_target = injector.finance_config["imperfection_targets"][
        "transaction_amount_outliers"
    ]
    outlier_minimum = int(outlier_target["minimum_value"])
    expected_boundaries = set(injector.config["boundary_dates"])

    print("imperfection_checks:")
    print(f"  budget_near_duplicates: {duplicate_rows}")
    print(f"  fx_rates.rate_empty: {int(fx_rates['rate'].astype(str).eq('').sum())}")
    outliers = int(
        (transactions["total_amount"].astype(float) >= outlier_minimum).sum()
    )
    print(f"  transaction_amount_outliers_ge_{outlier_minimum}: {outliers}")
    boundary_count = int(
        transactions["transaction_date"].astype(str).isin(expected_boundaries).sum()
    )
    print(f"  transaction_boundary_dates: {boundary_count}")


def _non_empty_values(values: Any) -> set[Any]:
    return {value for value in values.tolist() if str(value) != ""}


__all__ = [
    "print_distribution_summary",
    "print_imperfection_summary",
    "print_relationship_summary",
]
