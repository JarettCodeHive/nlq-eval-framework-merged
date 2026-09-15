"""Ground-truth verification harness (§14.2).

``SQLPulse`` executes each pair's ``reference_sql`` against a real CSV dataset
in DuckDB and returns the result as if it were the platform's answer.

This is NOT a stand-in for the platform and never scores it. It answers a
different question: *are the Q&A pairs and the dataset consistent with each
other?* §14.2 requires exactly this — "an independent reviewer re-executes
every reference_sql on a clean DuckDB instance" — before the package ships.

It is also how the pipeline gets exercised without spending platform calls. A
live question costs 50-170s and a token that lives an hour; validating 186
pairs this way takes seconds and no credentials.

Runs driven by it are PREVIEW-only and can never establish a release baseline
(see judge/cli.py). The platform under evaluation is only ever reached through
``judge.pulse_client`` (HC-4).
"""

from __future__ import annotations

from pathlib import Path

from judge.contracts import PulseResponse

MODULE_ROOT = Path(__file__).resolve().parent


class SQLPulse:
    """Pulse stand-in that executes each pair's reference_sql against a real
    CSV dataset (loaded via DuckDB). Returns the query result as platform_answer.

    Purpose: run the judge end-to-end against a real generated dataset before
    the actual Pulse API is reachable. Simulates 'perfect Pulse' if the pairs
    and dataset are consistent, or 'regressed Pulse' if they diverge — which
    is a useful test signal on its own.

    """

    def __init__(self, pairs: list[dict], dataset_dir: Path) -> None:
        try:
            import duckdb  # local import so judge module doesn't hard-depend on duckdb
        except ImportError as e:
            raise RuntimeError(
                "SQLPulse requires duckdb. `pip install duckdb` or add it to requirements."
            ) from e

        dataset_dir = Path(dataset_dir)
        if not dataset_dir.is_dir():
            raise FileNotFoundError(f"SQLPulse dataset dir not found: {dataset_dir}")

        self._by_id: dict[str, dict] = {p["question_id"]: p for p in pairs}
        self._conn = duckdb.connect(":memory:")
        for csv_path in sorted(dataset_dir.glob("*.csv")):
            table = csv_path.stem
            self._conn.execute(
                f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?)",
                [str(csv_path)],
            )
        self._dataset_dir = dataset_dir

    def query(self, question_id: str) -> PulseResponse:
        pair = self._by_id.get(question_id)
        if pair is None:
            raise KeyError(f"SQLPulse: unknown question_id={question_id!r}")
        sql = pair.get("reference_sql", "").strip()
        if not sql:
            raise KeyError(
                f"SQLPulse: pair {question_id!r} has no reference_sql to execute"
            )
        try:
            rows = self._conn.execute(sql).fetchall()
        except (
            Exception
        ) as exc:  # noqa: BLE001 — surface as answer_text so judge scores it
            answer = (
                f"SQL_EXECUTION_ERROR: {type(exc).__name__}: {str(exc).splitlines()[0]}"
            )
            return PulseResponse(
                question_id=question_id, answer_text=answer, generated_sql=sql
            )

        if not rows:
            answer = ""
        elif len(rows) == 1 and len(rows[0]) == 1:
            answer = str(rows[0][0])
        elif rows and len(rows[0]) == 1:
            answer = "; ".join(str(r[0]) for r in rows)
        else:
            answer = "; ".join(" ".join(str(c) for c in r) for r in rows)
        return PulseResponse(
            question_id=question_id, answer_text=answer, generated_sql=sql
        )

    @property
    def mode(self) -> str:
        return f"sql[{self._dataset_dir.name}]"

    def available_ids(self) -> list[str]:
        return sorted(self._by_id)

    def close(self) -> None:
        self._conn.close()
