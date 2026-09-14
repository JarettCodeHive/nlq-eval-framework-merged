"""Execute one reference SQL, serialize the result, write a deterministic
verification log (scope doc Section 9.6). Shared by every generator step.

result_hash = SHA-256 of the CANONICAL serialized answer string (the
thing exact-match compares downstream), not of Python repr(rows).

The log carries no wall-clock timestamp and no execution time on purpose:
a clean-room re-run must produce byte-identical logs (HC-7). Provenance is
dataset_version + git.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .serialization import SerializationError, serialize
from .output_paths import qa_version

QA_VERSION = qa_version(Path(__file__).resolve().parent.parent)


@dataclass
class Result:
    status: str  # "ok" | "blocked" | "error"
    answer: str
    error: str | None
    rows: list
    result_hash: str
    columns: list = None  # result column names
    col_types: list = None  # inferred type per column (see _col_types)


def _col_types(rows: list, ncols: int) -> list:
    """Infer one type token per column from the first non-NULL value seen -
    used to declare the answer schema and the zero-tolerance numeric cells."""
    import datetime
    import decimal

    out = ["null"] * ncols
    for i in range(ncols):
        for row in rows:
            v = row[i]
            if v is None:
                continue
            if isinstance(v, bool):
                out[i] = "bool"
            elif isinstance(v, int):
                out[i] = "int"
            elif isinstance(v, decimal.Decimal):
                out[i] = "decimal"
            elif isinstance(v, float):
                out[i] = "float"
            elif isinstance(v, datetime.datetime):
                out[i] = "datetime"
            elif isinstance(v, datetime.date):
                out[i] = "date"
            else:
                out[i] = "text"
            break
    return out


def run(con, sql: str) -> Result:
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - report any DuckDB failure
        return Result("error", "", str(exc), [], "", [], [])
    try:
        answer, status, error = serialize(rows, cols), "ok", None
    except SerializationError as exc:
        answer, status, error = "", "blocked", str(exc)
    canon = answer if status == "ok" else f"<{status}:{error}>"
    return Result(
        status,
        answer,
        error,
        rows,
        hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        cols,
        _col_types(rows, len(cols)),
    )


def library_versions() -> dict:
    import duckdb
    import jinja2

    return {
        "python": sys.version.split()[0],
        "duckdb": duckdb.__version__,
        "jinja2": jinja2.__version__,
    }


def log_payload(
    *,
    question_id: str,
    tier: str,
    dataset_version: str,
    profile: str,
    sql: str,
    result: Result,
    domain: str,
    **extra,
) -> dict:
    return {
        "question_id": question_id,
        "domain": domain,
        "tier": tier,
        "qa_version": QA_VERSION,
        "dataset_version": dataset_version,
        "profile": profile,
        "reference_sql": sql,
        "execution_status": result.status,
        "error": result.error,
        "row_count_returned": len(result.rows),
        "result_hash": result.result_hash,
        "generated_expected_answer": result.answer,
        "deterministic": True,
        "library_versions": library_versions(),
        **extra,
    }


def write_payload(log_dir: Path, name: str, payload: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_log(log_dir: Path, name: str, **kw) -> None:
    write_payload(log_dir, name, log_payload(**kw))
