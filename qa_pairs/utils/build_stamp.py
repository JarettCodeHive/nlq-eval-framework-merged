"""Provenance guard: prove the DuckDB a generator is about to read is the
one `generate_crm.py` built from the **authoritative** source bundle, and
that nobody has hand-edited its tables since.

`generate_crm.py` computes a build fingerprint over:
  * the dataset version tag
  * the authoritative DDL and DBML file hashes
  * every source CSV's hash + row count
  * a deterministic per-table content digest read back from the built DB

It stamps that fingerprint (and the per-table digests) into a `_build_stamp`
table inside the DuckDB and records the same values in the manifest.

`require_fresh_db()` runs at the top of every downstream step (scale_pairs,
author, rephrase). It re-derives the per-table digests from the open
connection and re-hashes the authoritative bundle, so a stale DB, a
hand-edited table, a changed source CSV, or an edited DDL/DBML all abort
the run instead of silently poisoning the answer key (scope 9.6 / HC-7).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from utils.dataset_source import resolve_dataset_source

STAMP_TABLE = "_build_stamp"
_RS = b"\x1e"  # ASCII record separator between serialized rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_digest(con, table: str) -> str:
    """Deterministic content hash of one table: every row, fully ordered by
    all columns so physical row order does not matter. Independent of the
    CSV, so a hand-edited DuckDB is caught even when the CSV is untouched."""
    ncols = len(con.execute(f'SELECT * FROM "{table}" LIMIT 0').description)
    order = ", ".join(str(i) for i in range(1, ncols + 1))
    h = hashlib.sha256()
    for row in con.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall():
        h.update(repr(row).encode("utf-8"))
        h.update(_RS)
    return h.hexdigest()


def table_digests(con, tables: list[str]) -> dict[str, str]:
    return {t: table_digest(con, t) for t in sorted(tables)}


def fingerprint(core: dict) -> str:
    """Hash of everything an `expected_answer` depends on. `core` carries
    dataset_version, schema_ddl_sha256, schema_dbml_sha256, files[], and
    table_digests{}."""
    payload = {
        "dataset_version": core["dataset_version"],
        "schema_ddl_sha256": core["schema_ddl_sha256"],
        "schema_dbml_sha256": core["schema_dbml_sha256"],
        "files": sorted(
            ({"name": f["name"], "sha256": f["sha256"], "rows": f["rows"]} for f in core["files"]),
            key=lambda f: f["name"],
        ),
        "table_digests": dict(sorted(core["table_digests"].items())),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def manifest_core(manifest: dict) -> dict:
    return {
        k: manifest[k]
        for k in (
            "dataset_version",
            "schema_ddl_sha256",
            "schema_dbml_sha256",
            "files",
            "table_digests",
        )
    }


def stamp(con, fp: str, digests: dict[str, str], dataset_version: str, generated_at: str) -> None:
    con.execute(f"DROP TABLE IF EXISTS {STAMP_TABLE}")
    con.execute(
        f"CREATE TABLE {STAMP_TABLE} "
        "(fingerprint VARCHAR, table_digests VARCHAR, "
        " dataset_version VARCHAR, generated_at VARCHAR)"
    )
    con.execute(
        f"INSERT INTO {STAMP_TABLE} VALUES (?, ?, ?, ?)",
        [fp, json.dumps(digests, sort_keys=True), dataset_version, generated_at],
    )


def _stamp_row(con):
    exists = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [STAMP_TABLE],
    ).fetchone()
    if not exists:
        return None
    return con.execute(f"SELECT fingerprint, table_digests FROM {STAMP_TABLE}").fetchone()


def require_fresh_db(
    con,
    manifest: dict,
    base_dir: Path,
    profile: str,
    generator_dir: str,
    staged_dir: Path,
) -> None:
    """Abort unless the open DuckDB, the manifest, and the authoritative
    source bundle all agree - down to per-table content digests.

    ``generator_dir`` names the domain's subfolder under ``generator/`` whose
    ``config.json`` / DDL / DBML are authoritative for this dataset (e.g.
    ``"crm"``, ``"sales"``). ``staged_dir`` is where that domain's driver
    script copies the source CSVs during staging (e.g. ``dataset/<profile>``
    for CRM, ``dataset/sales/<profile>`` for Sales) - required rather than
    assumed, since domains are free to lay their staged copies out
    differently."""

    def die(msg: str):
        raise SystemExit(
            f"provenance check failed (profile '{profile}'): {msg} - "
            f"rerun the generator/{generator_dir} stage script for --profile {profile}"
        )

    want = manifest.get("build_fingerprint")
    if not want:
        die("manifest has no build_fingerprint")
    if "table_digests" not in manifest:
        die("manifest has no table_digests")

    row = _stamp_row(con)
    if row is None:
        die(f"the DuckDB has no {STAMP_TABLE} table")
    db_fp, db_digests_json = row
    if db_fp != want:
        die(f"db fingerprint {db_fp!r} != manifest {want!r}")

    # 1. live per-table digests - catches a hand-edited DuckDB even if every
    #    CSV on disk is untouched.
    live = table_digests(con, list(manifest["table_digests"]))
    if live != manifest["table_digests"]:
        changed = sorted(t for t in live if live[t] != manifest["table_digests"].get(t))
        die(f"DuckDB table contents changed since build: {changed}")
    if json.loads(db_digests_json) != manifest["table_digests"]:
        die("_build_stamp digests disagree with the manifest")

    # 2. generator-owned source and canonical schema files.
    source = resolve_dataset_source(base_dir, profile, generator_dir)
    if manifest["dataset_version"] != source.dataset_version:
        die("manifest dataset version differs from the configured source")
    for label, path, want_sha in (
        ("canonical DDL", source.ddl_path, manifest["schema_ddl_sha256"]),
        ("canonical DBML", source.dbml_path, manifest["schema_dbml_sha256"]),
    ):
        if not path.exists():
            die(f"{label} missing")
        if _sha256_file(path) != want_sha:
            die(f"{label} changed since the manifest was written")

    # 3. source CSVs and their staged copies.
    for f in manifest["files"]:
        for label, csv_path in (
            (f"source {f['name']}", source.csv_dir / f["name"]),
            (f"staged {f['name']}", staged_dir / f["name"]),
        ):
            if not csv_path.exists():
                die(f"{label} missing")
            if _sha256_file(csv_path) != f["sha256"]:
                die(f"{label} changed since the manifest was written")

    # 4. the manifest's own fingerprint must match its own contents.
    if fingerprint(manifest_core(manifest)) != want:
        die("manifest build_fingerprint does not match its own contents")
