"""
Step 2 of the Logistics Q&A pipeline: DuckDB integrity checks on the staged
dataset. Mirrors ``validate_finance.py``, but checks Logistics's own declared
join paths (config/generation/logistics/validation.json) and imperfection
targets (config/generation/logistics/generation.json:imperfection_targets)
instead of Finance's.

    python generator/logistics/validate_logistics.py --profile dev
    python generator/logistics/validate_logistics.py --profile full

Checks:
  * every declared FK resolves (no orphan child rows)
  * every INNER JOIN path returns at least one row
  * every LEFT JOIN path leaves at least one unmatched parent row
  * the controlled imperfections are present

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
    ("shipments.order_id -> orders", "shipments", "order_id", "orders", "order_id"),
    ("shipments.carrier_id -> carriers", "shipments", "carrier_id", "carriers", "carrier_id"),
    (
        "inventory.warehouse_id -> warehouses",
        "inventory",
        "warehouse_id",
        "warehouses",
        "warehouse_id",
    ),
]

INNER_PATHS = {
    "shipments x orders (logistics_jp_001)": (
        "SELECT COUNT(*) FROM shipments s JOIN orders o ON s.order_id = o.order_id"
    ),
    "shipments x carriers (logistics_jp_002)": (
        "SELECT COUNT(*) FROM shipments s JOIN carriers c ON s.carrier_id = c.carrier_id"
    ),
    "inventory x warehouses (logistics_jp_003)": (
        "SELECT COUNT(*) FROM inventory i JOIN warehouses w ON i.warehouse_id = w.warehouse_id"
    ),
    "orders x warehouses valid subset (logistics_jp_005)": (
        "SELECT COUNT(*) FROM orders o JOIN warehouses w ON o.warehouse_id = w.warehouse_id"
    ),
    "orders x shipments x carriers (logistics_jp_006)": (
        "SELECT COUNT(*) FROM orders o JOIN shipments s ON o.order_id = s.order_id "
        "JOIN carriers c ON s.carrier_id = c.carrier_id"
    ),
}

LEFT_UNMATCHED = {
    "orders with NULL or orphan warehouse_id (logistics_jp_004)": (
        "SELECT COUNT(*) FROM orders o LEFT JOIN warehouses w ON o.warehouse_id = w.warehouse_id "
        "WHERE w.warehouse_id IS NULL"
    ),
}

IMPERFECTIONS = {
    "near-duplicate shipments": (
        "SELECT COUNT(*) FROM (SELECT order_id, carrier_id, tracking_number FROM shipments "
        "GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)"
    ),
    "orders with a NULL warehouse_id": "SELECT COUNT(*) FROM orders WHERE warehouse_id IS NULL",
    "orders with an orphaned (non-existent) warehouse_id": (
        "SELECT COUNT(*) FROM orders o WHERE o.warehouse_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM warehouses w WHERE w.warehouse_id = o.warehouse_id)"
    ),
    "order-total outliers (>=750,000)": "SELECT COUNT(*) FROM orders WHERE total_amount >= 750000",
    "boundary-date orders": (
        "SELECT COUNT(*) FROM orders WHERE order_date IN "
        "('1900-01-01', '2038-01-19', '2099-12-31', '2026-08-01')"
    ),
}


def connect(profile: str):
    return connect_typed(BASE / "dataset" / f"logistics_{profile}.duckdb")


def validate(profile: str) -> None:
    """Validate the staged Logistics dataset used for Q&A generation."""

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

    manifest = BASE / "dataset" / f"manifest_logistics_{profile}.json"
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
