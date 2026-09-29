"""
Step 1 of the Finance Q&A pipeline. Mirrors ``generator/sales/generate_sales.py``
exactly, but reads Finance-specific data files (``generator/finance/config.json``,
``finance_ddl.sql``) instead of assuming Sales's schema is the only one in the
repo.

The Finance golden dataset is generated upstream (Track A, ``generators/finance``).
This script does NOT regenerate that data - it stages the configured
generator output into ``dataset/finance/<profile>/``, builds an on-disk DuckDB
file, and writes a manifest with SHA-256 hashes so the rest of the pipeline
has a single, reproducible source of ground truth.

    python generator/finance/generate_finance.py --profile dev
    python generator/finance/generate_finance.py --profile full

Ground-truth rule (mirrors scope doc Section 9.6): every Q&A `expected_answer`
is computed by executing `reference_sql` against these CSVs via DuckDB, never
hand-typed and never against generator state.

Finance writes to a domain-namespaced slice of ``dataset/`` (``finance/<profile>``,
``finance_<profile>.duckdb``, ``manifest_finance_<profile>.json``) so it can
never collide with CRM's or Sales's own namespaced slices living in the same
directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))
from utils import build_stamp  # noqa: E402
from utils.dataset_source import resolve_dataset_source  # noqa: E402
from utils.verify import library_versions  # noqa: E402

DATASET = BASE / "dataset"

# parent-before-child so FK constraints hold on COPY
LOAD_ORDER = ["accounts", "transactions", "ledger_entries", "budgets", "fx_rates"]

COPY_OPTS = (
    "(FORMAT CSV, HEADER, DELIMITER ',', NULL '', "
    "DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_SELF_REF_FK = re.compile(
    r",\s*CONSTRAINT fk_accounts_parent_account\s*"
    r"FOREIGN KEY \(parent_account_id\) REFERENCES accounts \(account_id\)"
)


def load_duckdb(con, csv_dir: Path, ddl_path: Path) -> list[dict]:
    """Create the schema from the DDL and COPY every source CSV in FK order.
    Returns the manifest `files` list. Shared with the clean-room test.

    DuckDB 0.10 cannot validate accounts.parent_account_id's self-referencing
    FK against rows inserted by the same bulk COPY (fails regardless of row
    order), so that one constraint is dropped from the staging DDL only - the
    frozen ``schemas/finance/finance_ddl.sql`` contract file is untouched.
    Integrity is instead verified manually via anti-join in ``_integrity()``.
    """
    ddl_text, n = _SELF_REF_FK.subn("", ddl_path.read_text(encoding="utf-8"), count=1)
    if n != 1:
        raise SystemExit("expected exactly one self-referencing accounts FK constraint in DDL")
    con.execute(ddl_text)
    files = []
    for name in LOAD_ORDER:
        csv_path = csv_dir / f"{name}.csv"
        con.execute(f"COPY {name} FROM '{csv_path.as_posix()}' {COPY_OPTS};")
        rows = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = len(con.execute(f"PRAGMA table_info('{name}')").fetchall())
        files.append(
            {
                "name": f"{name}.csv",
                "sha256": sha256(csv_path),
                "rows": rows,
                "columns": cols,
            }
        )
    return files


def build(profile: str) -> None:
    source = resolve_dataset_source(BASE, profile, "finance")
    src_dir = source.csv_dir
    if not src_dir.is_dir():
        raise SystemExit(f"source CSVs not found for profile '{profile}': {src_dir}")
    for label, path in (
        ("Finance DDL", source.ddl_path),
        ("Finance DBML", source.dbml_path),
    ):
        if not path.is_file():
            raise SystemExit(f"{label} not found: {path}")

    out_dir = DATASET / "finance" / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = DATASET / f"finance_{profile}.duckdb"
    tmp_path = DATASET / f"finance_{profile}.duckdb.tmp"
    tmp_path.unlink(missing_ok=True)

    base_cfg = json.loads(source.base_config_path.read_text(encoding="utf-8"))
    release_cfg = json.loads(source.release_config_path.read_text(encoding="utf-8"))

    # Build into a temp DB + temp staging dir; the previous release is left
    # untouched until everything below has passed.
    con = duckdb.connect(str(tmp_path))
    try:
        files = load_duckdb(con, src_dir, source.ddl_path)
        for f in files:
            print(f"  {f['name']:<16} {f['rows']:>8,} rows  sha256={f['sha256'][:12]}")
        integrity = _integrity(con)
        _assert_integrity(integrity)  # raises before anything is published
        digests = build_stamp.table_digests(con, LOAD_ORDER)
        ddl_sha = sha256(source.ddl_path)
        dbml_sha = sha256(source.dbml_path)
        core = {
            "dataset_version": source.dataset_version,
            "schema_ddl_sha256": ddl_sha,
            "schema_dbml_sha256": dbml_sha,
            "files": files,
            "table_digests": digests,
        }
        fp = build_stamp.fingerprint(core)
        build_stamp.stamp(
            con,
            fp,
            digests,
            source.dataset_version,
            release_cfg["fixed_values"]["manifest_generated_at"],
        )
    except BaseException:
        con.close()
        tmp_path.unlink(missing_ok=True)  # never leave a half-built DB behind
        raise
    con.close()

    manifest = {
        "domain": "finance",
        "dataset_version": source.dataset_version,
        "build_fingerprint": fp,
        "source_path": source.source_label,
        "profile": profile,
        "generated_at": release_cfg["fixed_values"]["manifest_generated_at"],
        "seed": base_cfg["seed"],
        "reference_today": base_cfg["reference_today"],
        "row_cap_per_table": base_cfg["max_rows_per_table"],
        "schema_ddl_sha256": ddl_sha,
        "schema_dbml_sha256": dbml_sha,
        "duckdb_file": db_path.name,
        "files": files,
        "table_digests": digests,
        "integrity": integrity,
        "library_versions": library_versions(),
    }

    # Everything passed - now publish atomically: stage CSVs, swap the DB in
    # with os.replace (atomic on the same filesystem), then write the manifest.
    for name in LOAD_ORDER:
        shutil.copy2(src_dir / f"{name}.csv", out_dir / f"{name}.csv")
    os.replace(tmp_path, db_path)
    (DATASET / f"manifest_finance_{profile}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\ndataset/finance/{profile}/  +  {db_path.name}  +  manifest_finance_{profile}.json")
    print(
        f"reference_today = {manifest['reference_today']}  "
        f"integrity: {integrity['row_cap_check']}, fk {integrity['fk_check']}"
    )


def _integrity(con) -> dict:
    """Manifest integrity block, mirroring generate_sales.py's block but
    against Finance's declared join paths (config/generation/finance/validation.json
    finance_jp_002 and finance_jp_001)."""
    over_cap = con.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM accounts UNION ALL "
        "SELECT COUNT(*) FROM transactions UNION ALL SELECT COUNT(*) FROM ledger_entries "
        "UNION ALL SELECT COUNT(*) FROM budgets UNION ALL SELECT COUNT(*) FROM fx_rates)"
    ).fetchone()[0]
    inner = con.execute(
        "SELECT COUNT(*) FROM ledger_entries le JOIN transactions t "
        "ON le.transaction_id = t.transaction_id"
    ).fetchone()[0]
    left_unmatched = con.execute(
        "SELECT COUNT(*) FROM transactions t LEFT JOIN ledger_entries le "
        "ON t.transaction_id = le.transaction_id WHERE le.entry_id IS NULL"
    ).fetchone()[0]
    # accounts.parent_account_id's self-referencing FK is not DDL-enforced during
    # COPY (see load_duckdb docstring), so it is checked here via anti-join.
    self_ref_orphans = con.execute(
        "SELECT COUNT(*) FROM accounts c WHERE c.parent_account_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM accounts p WHERE p.account_id = c.parent_account_id)"
    ).fetchone()[0]
    return {
        "fk_check": "passed" if self_ref_orphans == 0 else f"FAILED ({self_ref_orphans} orphans)",
        "row_cap_check": "passed" if over_cap <= 250000 else f"FAILED ({over_cap})",
        "inner_join_paths_verified": inner > 0,
        "left_join_unmatched_rows_present": left_unmatched > 0,
    }


def _assert_integrity(block: dict) -> None:
    """A failed integrity check aborts before any manifest or database is
    published - the manifest must never record a dataset that did not pass."""
    failures = []
    if block["fk_check"] != "passed":
        failures.append(f"fk_check={block['fk_check']}")
    if block["row_cap_check"] != "passed":
        failures.append(f"row_cap_check={block['row_cap_check']}")
    if not block["inner_join_paths_verified"]:
        failures.append("inner_join_paths_verified=False")
    if not block["left_join_unmatched_rows_present"]:
        failures.append("left_join_unmatched_rows_present=False")
    if failures:
        raise SystemExit("INTEGRITY FAILURE: " + ", ".join(failures))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    build(ap.parse_args().profile)


if __name__ == "__main__":
    main()
