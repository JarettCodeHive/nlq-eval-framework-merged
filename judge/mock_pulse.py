"""Offline Pulse stand-ins for CI, dev, and pre-flight plumbing tests.

The real client is `judge.pulse_client.PulseClient` (`--pulse live`). These three
mirror its interface — `query(question_id) -> PulseResponse`, `mode`,
`available_ids` — but need no network:

- `MockPulse`   — canned fixture responses keyed by question_id ("good" /
  "regressed" / "bad" sets; the good/regressed pair makes the scorecard's ±5 pp
  flag visible in a demo).
- `EchoPulse`   — replays each pair's expected_answer as the platform answer.
- `SQLPulse`    — executes each pair's reference_sql against a CSV dataset.

Only `answer_text` and `generated_sql` matter to the judge (HC-4).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

MODULE_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = MODULE_ROOT / "data" / "pulse_fixtures"


class PulseResponse(BaseModel):
    question_id: str
    answer_text: str
    generated_sql: str | None = None
    # Any other Pulse-side fields (latency, model_used, follow_up_suggestions,
    # dashboard payload) would land here but are not scored.


class MockPulse:
    """Return the canned response for a question_id in the chosen mode.

    Fixtures live at data/pulse_fixtures/<mode>/<question_id>.json. Missing
    fixture = KeyError, deliberately loud — silent fallbacks make it easy to
    ship a demo that scores nothing.
    """

    def __init__(self, mode: str = "good", fixtures_dir: Path | None = None) -> None:
        self._mode = mode
        self._root = (fixtures_dir or FIXTURES_DIR) / mode
        self._aggregate_path = (fixtures_dir or FIXTURES_DIR) / f"{mode}.json"
        self._aggregate: dict[str, dict] | None = None
        if self._aggregate_path.is_file():
            self._aggregate = json.loads(
                self._aggregate_path.read_text(encoding="utf-8")
            )
        elif not self._root.is_dir():
            raise FileNotFoundError(
                f"MockPulse: fixtures directory not found: {self._root}\n"
                f"Expected layout: data/pulse_fixtures/{{good,regressed}}/<qid>.json "
                f"or data/pulse_fixtures/{{good,regressed}}.json"
            )

    def query(self, question_id: str) -> PulseResponse:
        if self._aggregate is not None:
            payload = self._aggregate.get(question_id)
            if payload is None:
                raise KeyError(
                    f"MockPulse: no fixture for question_id={question_id!r} in mode={self._mode!r}. "
                    f"Expected: {self._aggregate_path}"
                )
            return PulseResponse.model_validate(payload)

        path = self._root / f"{question_id}.json"
        if not path.is_file():
            raise KeyError(
                f"MockPulse: no fixture for question_id={question_id!r} in mode={self._mode!r}. "
                f"Expected: {path}"
            )
        return PulseResponse.model_validate_json(path.read_text(encoding="utf-8"))

    @property
    def mode(self) -> str:
        return self._mode

    def available_ids(self) -> list[str]:
        if self._aggregate is not None:
            return sorted(self._aggregate)
        return sorted(p.stem for p in self._root.glob("*.json"))


class EchoPulse:
    """Perfect-Pulse stand-in — returns each pair's expected_answer as the
    platform_answer, and reference_sql as the generated_sql.

    Purpose: run the judge end-to-end against real authored pairs BEFORE the
    real Pulse API is reachable. Since the "platform" is returning the ground
    truth, judge scores should skew high — this is a plumbing test, not a
    discrimination test. Swap for PulseClient once DEP-03 unblocks.

    Interface parity with MockPulse: `query(question_id)` -> PulseResponse.
    """

    def __init__(self, pairs: list[dict]) -> None:
        self._by_id: dict[str, dict] = {p["question_id"]: p for p in pairs}

    def query(self, question_id: str) -> PulseResponse:
        pair = self._by_id.get(question_id)
        if pair is None:
            raise KeyError(f"EchoPulse: unknown question_id={question_id!r}")
        return PulseResponse(
            question_id=question_id,
            answer_text=str(pair.get("expected_answer", "")),
            generated_sql=(pair.get("reference_sql") or None),
        )

    @property
    def mode(self) -> str:
        return "echo"

    def available_ids(self) -> list[str]:
        return sorted(self._by_id)


class SQLPulse:
    """Pulse stand-in that executes each pair's reference_sql against a real
    CSV dataset (loaded via DuckDB). Returns the query result as platform_answer.

    Purpose: run the judge end-to-end against a real generated dataset before
    the actual Pulse API is reachable. Simulates 'perfect Pulse' if the pairs
    and dataset are consistent, or 'regressed Pulse' if they diverge — which
    is a useful test signal on its own.

    Interface parity with MockPulse: `query(question_id)` -> PulseResponse.
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


def load_pairs(path: Path | None = None) -> list[dict]:
    """Load the demo Q&A pair set from data/sample_pairs.json or a CSV later via CLI."""
    p = path or MODULE_ROOT / "data" / "sample_pairs.json"
    return json.loads(p.read_text(encoding="utf-8"))
