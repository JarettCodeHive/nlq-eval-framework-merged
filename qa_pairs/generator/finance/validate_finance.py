"""
Step 2 of the Finance Q&A pipeline: DuckDB integrity checks on the staged
dataset. Mirrors ``validate_sales.py``, but checks Finance's own declared join
paths (config/generation/finance/validation.json) and imperfection targets
(config/generation/finance/generation.json:imperfection_targets) instead of
Sales's.

    python generator/finance/validate_finance.py --profile dev
    python generator/finance/validate_finance.py --profile full

Checks:
  * every declared FK resolves (no orphan child rows)
  * every INNER JOIN path returns at least one row
  * every LEFT JOIN path leaves at least one unmatched parent row
  * the four controlled imperfections are present
  * the transaction-to-fx_rates composite lookup covers every transaction

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE.parent))
from qa_pairs.utils.duckdb_io import connect_typed  # noqa: E402

FK_CHECKS = [
    (
        "ledger_entries.transaction_id -> transactions",
        "ledger_entries",
        "transaction_id",
        "transactions",
        "transaction_id",
    ),
    (
        "ledger_entries.account_id -> accounts",
        "ledger_entries",
        "account_id",
        "accounts",
        "account_id",
    ),
    (
        "budgets.account_id -> accounts",
        "budgets",
        "account_id",
        "accounts",
        "account_id",
    ),
    (
        "accounts.parent_account_id -> accounts",
        "accounts",
        "parent_account_id",
        "accounts",
        "account_id",
    ),
]

FX_COMPOSITE_JOIN = (
    "t.source_currency = f.from_currency AND t.target_currency = f.to_currency "
    "AND t.transaction_date = f.rate_date"
)

INNER_PATHS = {
    "ledger_entries x transactions (finance_jp_002)": (
        "SELECT COUNT(*) FROM ledger_entries le JOIN transactions t "
        "ON le.transaction_id = t.transaction_id"
    ),
    "ledger_entries x accounts (finance_jp_003)": (
        "SELECT COUNT(*) FROM ledger_entries le JOIN accounts a ON le.account_id = a.account_id"
    ),
    "budgets x accounts (finance_jp_004)": (
        "SELECT COUNT(*) FROM budgets b JOIN accounts a ON b.account_id = a.account_id"
    ),
    "accounts x parent accounts (finance_jp_005 matched)": (
        "SELECT COUNT(*) FROM accounts c JOIN accounts p ON c.parent_account_id = p.account_id"
    ),
    "transactions x fx_rates (finance_jp_006)": (
        f"SELECT COUNT(*) FROM transactions t JOIN fx_rates f ON {FX_COMPOSITE_JOIN}"
    ),
    "transactions x available fx_rates (finance_jp_007)": (
        f"SELECT COUNT(*) FROM transactions t JOIN fx_rates f "
        f"ON {FX_COMPOSITE_JOIN} AND f.rate IS NOT NULL"
    ),
    "transactions x missing fx_rates (finance_jp_008)": (
        f"SELECT COUNT(*) FROM transactions t JOIN fx_rates f "
        f"ON {FX_COMPOSITE_JOIN} AND f.rate IS NULL"
    ),
    "transactions x ledger_entries x accounts (finance_jp_009)": (
        "SELECT COUNT(*) FROM transactions t JOIN ledger_entries le "
        "ON t.transaction_id = le.transaction_id JOIN accounts a ON le.account_id = a.account_id"
    ),
}

LEFT_UNMATCHED = {
    "transactions without ledger entries (finance_jp_001)": (
        "SELECT COUNT(*) FROM transactions t LEFT JOIN ledger_entries le "
        "ON t.transaction_id = le.transaction_id WHERE le.entry_id IS NULL"
    ),
    "root accounts without a parent (finance_jp_005 unmatched)": (
        "SELECT COUNT(*) FROM accounts c LEFT JOIN accounts p "
        "ON c.parent_account_id = p.account_id WHERE p.account_id IS NULL"
    ),
}

IMPERFECTIONS = {
    "missing FX rates": "SELECT COUNT(*) FROM fx_rates WHERE rate IS NULL",
    "near-duplicate budgets": (
        "SELECT COUNT(*) FROM (SELECT account_id, fiscal_year, period_start, period_end, "
        "scenario FROM budgets GROUP BY 1, 2, 3, 4, 5 HAVING COUNT(*) > 1)"
    ),
    "transaction-amount outliers (>=25,000,000)": (
        "SELECT COUNT(*) FROM transactions WHERE total_amount >= 25000000"
    ),
    "boundary-date transactions": (
        "SELECT COUNT(*) FROM transactions WHERE transaction_date IN "
        "('1900-01-01', '2038-01-19', '2099-12-31', '2026-08-01')"
    ),
}


def connect(profile: str):
    return connect_typed(BASE / "dataset" / f"finance_{profile}.duckdb")


def validate(profile: str) -> None:
    """Validate the staged Finance dataset used for Q&A generation."""

    con = connect(profile)
    failures = 0

    print(f"[{profile}] FK integrity")
    for label, ct, cc, pt, pc in FK_CHECKS:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {ct} x WHERE x.{cc} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {pt} p WHERE p.{pc} = x.{cc})"
        ).fetchone()[0]
        ok = orphans == 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {orphans} orphan(s)")

    print(f"[{profile}] INNER JOIN paths (must be non-empty)")
    for label, sql in INNER_PATHS.items():
        n = con.execute(sql).fetchone()[0]
        ok = n > 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {n:,} rows")

    print(f"[{profile}] LEFT JOIN paths (must have unmatched rows)")
    for label, sql in LEFT_UNMATCHED.items():
        n = con.execute(sql).fetchone()[0]
        ok = n > 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {n:,} unmatched")

    print(f"[{profile}] fx_rates composite lookup coverage (finance_jp_006)")
    total_txn = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    matched_txn = con.execute(
        f"SELECT COUNT(*) FROM transactions t JOIN fx_rates f ON {FX_COMPOSITE_JOIN}"
    ).fetchone()[0]
    coverage_ok = matched_txn == total_txn
    failures += not coverage_ok
    print(
        f"  {'ok ' if coverage_ok else 'FAIL'} every transaction has exactly one fx_rates match: "
        f"{matched_txn:,} / {total_txn:,}"
    )

    print(
        f"[{profile}] controlled imperfections (present at full scale; "
        f"exact-match proxies may read 0 on the dev sample)"
    )
    for label, sql in IMPERFECTIONS.items():
        n = con.execute(sql).fetchone()[0]
        if n > 0:
            mark = "ok  "
        elif profile == "dev":
            mark = "warn"  # dev sample is too small to guarantee every type
        else:
            mark = "FAIL"
            failures += 1
        print(f"  {mark} {label}: {n:,}")

    manifest = BASE / "dataset" / f"manifest_finance_{profile}.json"
    print(
        f"[{profile}] manifest: {json.loads(manifest.read_text())['dataset_version']}"
    )

    print(
        f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}"
    )
    con.close()
    if failures:
        raise ValueError(f"{failures} Q&A dataset validation check(s) failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    try:
        validate(ap.parse_args().profile)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
