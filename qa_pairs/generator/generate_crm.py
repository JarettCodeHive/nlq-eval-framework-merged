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
import shutil
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "data" / "crm_dataset_v2"
DATASET = BASE / "dataset"
DDL = BASE / "schema" / "ddl.sql"

# parent-before-child so FK constraints hold on COPY
LOAD_ORDER = ["accounts", "campaigns", "contacts", "contact_campaigns",
              "interactions", "support_cases"]

COPY_OPTS = ("(FORMAT CSV, HEADER, DELIMITER ',', NULL '', "
             "DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(profile: str) -> None:
    src_dir = SOURCE / profile
    if not src_dir.is_dir():
        raise SystemExit(f"source CSVs not found: {src_dir}")

    out_dir = DATASET / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in LOAD_ORDER:
        shutil.copy2(src_dir / f"{name}.csv", out_dir / f"{name}.csv")

    db_path = DATASET / f"crm_{profile}.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute(DDL.read_text(encoding="utf-8"))

    base_cfg = json.loads((BASE / "generator" / "base.json").read_text())
    files = []
    for name in LOAD_ORDER:
        csv_path = out_dir / f"{name}.csv"
        con.execute(f"COPY {name} FROM '{csv_path.as_posix()}' {COPY_OPTS};")
        rows = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = len(con.execute(f"PRAGMA table_info('{name}')").fetchall())
        files.append({"name": f"{name}.csv", "sha256": sha256(csv_path),
                      "rows": rows, "columns": cols})
        print(f"  {name:<18} {rows:>8,} rows  sha256={files[-1]['sha256'][:12]}")
    con.close()

    manifest = {
        "domain": "crm",
        "dataset_version": f"crm_dataset_v2-{profile}",
        "profile": profile,
        "seed": base_cfg["seed"],
        "reference_today": base_cfg["reference_today"],
        "source_bundle": "crm_dataset_v2",
        "duckdb_file": db_path.name,
        "files": files,
        "imperfections": base_cfg["imperfections"],
    }
    (DATASET / f"manifest_{profile}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\ndataset/{profile}/  +  {db_path.name}  +  manifest_{profile}.json")
    print(f"reference_today = {manifest['reference_today']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    build(ap.parse_args().profile)


if __name__ == "__main__":
    main()
