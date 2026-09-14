"""
Step 1 of the CRM Q&A POC pipeline.

The CRM golden dataset is generated upstream (Track A) and delivered as
the frozen `crm_dataset_v2` bundle. This script does NOT regenerate that
data - it stages the exported CSVs into `dataset/<profile>/`, builds an
on-disk DuckDB file for DBeaver, and writes a manifest with SHA-256
hashes so the rest of the pipeline has a single, reproducible source of
ground truth.

    python generator/generate_crm.py --profile dev
    python generator/generate_crm.py --profile full

Ground-truth rule (scope doc Section 9.6): every Q&A `expected_answer`
is computed by executing `reference_sql` against these CSVs via DuckDB,
never hand-typed and never against generator state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from utils import build_stamp
from utils.verify import library_versions

SOURCE = BASE / "data" / "crm_dataset_v2"
DATASET = BASE / "dataset"
DDL = BASE / "schema" / "ddl.sql"

# parent-before-child so FK constraints hold on COPY
LOAD_ORDER = [
    "accounts",
    "campaigns",
    "contacts",
    "contact_campaigns",
    "interactions",
    "support_cases",
]

COPY_OPTS = (
    "(FORMAT CSV, HEADER, DELIMITER ',', NULL '', "
    "DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


DBML = BASE / "schema" / "er.dbml"
AUTH_DDL = SOURCE / "crm_ddl.sql"
AUTH_DBML = SOURCE / "crm_er.dbml"


def load_duckdb(con, csv_dir: Path) -> list[dict]:
    """Create the schema from the DDL and COPY every source CSV in FK order.
    Returns the manifest `files` list. Shared with the clean-room test."""
    con.execute(DDL.read_text(encoding="utf-8"))
    files = []
    for name in LOAD_ORDER:
        csv_path = csv_dir / f"{name}.csv"
        con.execute(f"COPY {name} FROM '{csv_path.as_posix()}' {COPY_OPTS};")
        rows = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = len(con.execute(f"PRAGMA table_info('{name}')").fetchall())
        files.append(
            {"name": f"{name}.csv", "sha256": sha256(csv_path), "rows": rows, "columns": cols}
        )
    return files


def build(profile: str) -> None:
    src_dir = SOURCE / profile
    if not src_dir.is_dir():
        raise SystemExit(f"source CSVs not found: {src_dir}")

    # the authoritative DDL/DBML must be byte-identical to the repo copies -
    # every hash downstream assumes this.
    for label, a, b in (("crm_ddl.sql", AUTH_DDL, DDL), ("crm_er.dbml", AUTH_DBML, DBML)):
        if a.read_bytes() != b.read_bytes():
            raise SystemExit(f"{label}: authoritative bundle != schema/ - reconcile first")

    out_dir = DATASET / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = DATASET / f"crm_{profile}.duckdb"
    tmp_path = DATASET / f"crm_{profile}.duckdb.tmp"
    tmp_path.unlink(missing_ok=True)

    base_cfg = json.loads((BASE / "generator" / "base.json").read_text())
    bundle_cfg = json.loads((SOURCE / "crm.json").read_text())

    # Build into a temp DB + temp staging dir; the previous release is left
    # untouched until everything below has passed.
    con = duckdb.connect(str(tmp_path))
    try:
        files = load_duckdb(con, src_dir)
        for f in files:
            print(f"  {f['name']:<22} {f['rows']:>8,} rows  sha256={f['sha256'][:12]}")
        integrity = _integrity(con)
        _assert_integrity(integrity)  # raises before anything is published
        digests = build_stamp.table_digests(con, LOAD_ORDER)
        ddl_sha, dbml_sha = sha256(DDL), sha256(DBML)
        core = {
            "dataset_version": bundle_cfg["dataset_version"],
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
            bundle_cfg["dataset_version"],
            bundle_cfg["fixed_values"]["manifest_generated_at"],
        )
    except BaseException:
        con.close()
        tmp_path.unlink(missing_ok=True)  # never leave a half-built DB behind
        raise
    con.close()

    manifest = {
        "domain": "crm",
        # canonical tag from the delivered bundle - preserved for traceability
        "dataset_version": bundle_cfg["dataset_version"],
        "build_fingerprint": fp,
        "source_bundle": "crm_dataset_v2",
        "profile": profile,
        # fixed per dataset version (scope doc Section 7.7) so a clean-room
        # re-run regenerates a byte-identical manifest
        "generated_at": bundle_cfg["fixed_values"]["manifest_generated_at"],
        "seed": base_cfg["seed"],
        "reference_today": base_cfg["reference_today"],
        "row_cap_per_table": base_cfg["max_rows_per_table"],
        "schema_ddl_sha256": ddl_sha,
        "schema_dbml_sha256": dbml_sha,
        "duckdb_file": db_path.name,
        "files": files,
        "table_digests": digests,
        "imperfections": base_cfg["imperfections"],
        "integrity": integrity,
        "library_versions": library_versions(),
    }

    # Everything passed - now publish atomically: stage CSVs, swap the DB in
    # with os.replace (atomic on the same filesystem), then write the manifest.
    for name in LOAD_ORDER:
        shutil.copy2(src_dir / f"{name}.csv", out_dir / f"{name}.csv")
    os.replace(tmp_path, db_path)
    (DATASET / f"manifest_{profile}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\ndataset/{profile}/  +  {db_path.name}  +  manifest_{profile}.json")
    print(
        f"reference_today = {manifest['reference_today']}  "
        f"integrity: {integrity['row_cap_check']}, fk {integrity['fk_check']}"
    )


def _integrity(con) -> dict:
    """Manifest integrity block (scope doc Appendix B)."""
    over_cap = con.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM accounts UNION ALL "
        "SELECT COUNT(*) FROM contacts UNION ALL SELECT COUNT(*) FROM campaigns "
        "UNION ALL SELECT COUNT(*) FROM contact_campaigns UNION ALL "
        "SELECT COUNT(*) FROM interactions UNION ALL SELECT COUNT(*) FROM support_cases)"
    ).fetchone()[0]
    inner = con.execute(
        "SELECT COUNT(*) FROM support_cases s JOIN accounts a ON s.account_id = a.account_id"
    ).fetchone()[0]
    left_unmatched = con.execute(
        "SELECT COUNT(*) FROM accounts a LEFT JOIN contacts c ON a.account_id = c.account_id "
        "WHERE c.contact_id IS NULL"
    ).fetchone()[0]
    return {
        "fk_check": "passed",  # DDL FK constraints enforced on COPY - a failure aborts load
        "row_cap_check": "passed" if over_cap <= 250000 else f"FAILED ({over_cap})",
        "inner_join_paths_verified": inner > 0,
        "left_join_unmatched_rows_present": left_unmatched > 0,
    }


def _assert_integrity(block: dict) -> None:
    """A failed integrity check aborts before any manifest or database is
    published (scope doc Appendix B - the manifest must never record a
    dataset that did not pass)."""
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
