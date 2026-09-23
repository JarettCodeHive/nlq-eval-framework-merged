"""
Step 2 of the Sales Q&A pipeline: DuckDB integrity checks on the staged
dataset. Mirrors ``validate_crm.py``, but checks Sales's own declared join
paths (config/generation/sales/validation.json) and imperfection targets
(config/generation/sales/generation.json:imperfection_targets) instead of
CRM's.

    python generator/validate_sales.py --profile dev
    python generator/validate_sales.py --profile full

Checks:
  * every declared FK resolves (no orphan child rows)
  * every INNER JOIN path returns at least one row
  * every LEFT JOIN path leaves at least one unmatched parent row
  * the four controlled imperfections are present

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))
from utils.duckdb_io import connect_typed  # noqa: E402

FK_CHECKS = [
    ("deals.lead_id -> leads", "deals", "lead_id", "leads", "lead_id"),
    ("quotations.deal_id -> deals", "quotations", "deal_id", "deals", "deal_id"),
    (
        "quotations.product_id -> products",
        "quotations",
        "product_id",
        "products",
        "product_id",
    ),
]

INNER_PATHS = {
    "deals x leads (sales_jp_002)": (
        "SELECT COUNT(*) FROM deals d JOIN leads l ON d.lead_id = l.lead_id"
    ),
    "deals x quotations (sales_jp_003)": (
        "SELECT COUNT(*) FROM deals d JOIN quotations q ON d.deal_id = q.deal_id"
    ),
    "products x quotations (sales_jp_004)": (
        "SELECT COUNT(*) FROM products p JOIN quotations q ON p.product_id = q.product_id"
    ),
    "deals x quotations x products (sales_jp_005)": (
        "SELECT COUNT(*) FROM deals d JOIN quotations q ON d.deal_id = q.deal_id "
        "JOIN products p ON q.product_id = p.product_id"
    ),
    "leads x deals x quotations x products (sales_jp_006)": (
        "SELECT COUNT(*) FROM leads l JOIN deals d ON l.lead_id = d.lead_id "
        "JOIN quotations q ON d.deal_id = q.deal_id JOIN products p ON q.product_id = p.product_id"
    ),
    "won deals x targets (sales_jp_008)": (
        "SELECT COUNT(*) FROM targets t JOIN deals d ON d.rep_name = t.rep_name "
        "AND d.close_date >= t.period_start AND d.close_date < t.period_end "
        "AND d.stage = 'Won'"
    ),
}

LEFT_UNMATCHED = {
    "leads without deals (sales_jp_001)": (
        "SELECT COUNT(*) FROM leads l LEFT JOIN deals d ON l.lead_id = d.lead_id "
        "WHERE d.deal_id IS NULL"
    ),
    "targets without won deals in period (sales_jp_007)": (
        "SELECT COUNT(*) FROM targets t LEFT JOIN deals d ON d.rep_name = t.rep_name "
        "AND d.close_date >= t.period_start AND d.close_date < t.period_end "
        "AND d.stage = 'Won' WHERE d.deal_id IS NULL"
    ),
}

IMPERFECTIONS = {
    "missing product list prices": "SELECT COUNT(*) FROM products WHERE list_price IS NULL",
    "near-duplicate quotation lines": (
        "SELECT COUNT(*) FROM (SELECT deal_id, product_id, quote_number FROM quotations "
        "GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)"
    ),
    "deal-amount outliers (>=2,500,000)": "SELECT COUNT(*) FROM deals WHERE deal_amount >= 2500000",
    "boundary-date product created_at": (
        "SELECT COUNT(*) FROM products WHERE created_at::DATE IN "
        "('1900-01-01', '2038-01-19', '2099-12-31', '2026-08-01')"
    ),
}


def connect(profile: str):
    return connect_typed(BASE / "dataset" / f"sales_{profile}.duckdb")


def validate(profile: str) -> None:
    """Validate the staged Sales dataset used for Q&A generation."""

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

    print(
        f"[{profile}] controlled imperfections (present at full scale; "
        f"exact-match proxies may read 0 on the 1%% dev sample)"
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

    manifest = BASE / "dataset" / f"manifest_sales_{profile}.json"
    print(f"[{profile}] manifest: {json.loads(manifest.read_text())['dataset_version']}")

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
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
