"""Translate a release CSV into the entity definition and rows Studio expects.

Deliberately reproduces what the Studio UI's own CSV import produces, verified
against a captured import of `accounts.csv`: a column whose every value parses
as a number becomes `Number`/`float64`, everything else becomes `Short Text`,
and no primary keys, foreign keys or relationships are declared.

That is lossy against `schemas/<domain>/<domain>_ddl.sql` — `is_active` is a
BOOLEAN and `created_at` a TIMESTAMP there, and both land here as text. The
loss is intentional for now: it is the configuration the platform was measured
under in the 2026-09-21 run, so reproducing it keeps later runs comparable.
Declaring the real types needs Studio's type vocabulary for dates and booleans,
which the captured import never exercised — see `studio/README.md`.

Values are passed through byte-for-byte. The CSVs already hold `true`/`false`
and ISO-8601 timestamps, and the UI import applied no transformation of its
own; the only decision is JSON number versus JSON string.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator

# The two type objects the UI emits, copied exactly. `Implementation` is null
# for text and carries the numeric width for numbers.
NUMBER_TYPE: dict[str, Any] = {
    "Name": "Number",
    "Class": "Number",
    "Implementation": {"Type": "float64"},
}
TEXT_TYPE: dict[str, Any] = {
    "Name": "Short Text",
    "Class": "Text",
    "Implementation": None,
}


def field_definition(name: str, *, numeric: bool) -> dict[str, Any]:
    """One entry of the `Fields` array in a create-entity request."""

    return {
        "Name": name,
        "Description": "",
        "Default": "",
        "Type": NUMBER_TYPE if numeric else TEXT_TYPE,
        "Optional": True,
        "Format": {"NumberFormat": "UserInput"} if numeric else None,
        "Relation": None,
        "Virtual": False,
        "DisplayOrder": 0,
        "DisplayLabel": name,
    }


def _parses_as_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def numeric_columns(rows: list[dict[str, str]], columns: list[str]) -> set[str]:
    """Columns whose every non-empty value is a number.

    Empty values are ignored rather than disqualifying: a nullable numeric FK
    such as `contacts.account_id` is still a number column, and typing it as
    text would silently change how the platform joins on it.
    """

    return {
        column
        for column in columns
        if any((row.get(column) or "").strip() for row in rows)
        and all(
            _parses_as_number(value)
            for row in rows
            if (value := (row.get(column) or "").strip())
        )
    }


def entity_definition(
    name: str, columns: list[str], numeric: set[str]
) -> dict[str, Any]:
    """The full body of `POST /org/{org}/entity`."""

    return {
        "Name": name,
        "Fields": [
            field_definition(column, numeric=column in numeric) for column in columns
        ],
        "DisplayFields": [],
        "RelationshipDef": False,
    }


def encode_value(value: str, *, numeric: bool) -> Any:
    """One CSV cell as Studio wants it on the wire.

    An empty cell becomes JSON null rather than an empty string, so a missing
    value reads as absent rather than as the text "". OI-4 already settled that
    an empty CSV field is a SQL NULL on our side; this keeps that true after
    the round trip. Every field is `Optional`, so null is always accepted.
    """

    text = (value or "").strip()
    if not text:
        return None
    if not numeric:
        return text
    # Integral values stay integers: the UI sends `"account_id": 1`, not 1.0,
    # and an id rendered as "1.0" in an answer would fail exact-match.
    try:
        return int(text)
    except ValueError:
        return float(text)


def encode_row(row: dict[str, str], columns: list[str], numeric: set[str]) -> dict:
    """One element of the `POST /org/{org}/entity/{id}/job` array."""

    return {
        "Fields": {
            column: encode_value(row.get(column, ""), numeric=column in numeric)
            for column in columns
        }
    }


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (column order, rows). Column order follows the CSV header."""

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    if not columns:
        raise ValueError(f"{path} has no header row")
    return columns, rows


def batched(rows: list[Any], size: int) -> Iterator[list[Any]]:
    """Split rows into bulk-load batches, preserving order."""

    for start in range(0, len(rows), max(1, size)):
        yield rows[start : start + size]


def batched_within(
    rows: list[Any], *, max_rows: int, max_bytes: int
) -> Iterator[list[Any]]:
    """Batch by row count AND serialised size, whichever binds first.

    The captured import sent 4800 rows of `accounts`, which serialise to almost
    exactly 1 MiB — so a row cap alone reproduces that request only because
    `accounts` happens to be a narrow table. `support_cases` is nearly twice as
    wide, and 4800 of its rows come to 1.7 MiB. The one batch the platform is
    known to have accepted was ~1 MiB, so a wider table should send fewer rows
    rather than a payload half again larger than anything observed working.

    A single row over `max_bytes` is still emitted alone — an empty batch would
    loop forever.
    """

    # The array's own `[`, `]` and `, ` separators are real bytes on the wire:
    # 4800 rows carry ~9.6 KB of them, which is enough to push a batch sized by
    # row content alone over a ceiling it was meant to respect.
    brackets = 2
    separator = 2

    batch: list[Any] = []
    size = brackets
    for row in rows:
        row_bytes = len(json.dumps(row)) + (separator if batch else 0)
        if batch and (len(batch) >= max_rows or size + row_bytes > max_bytes):
            yield batch
            batch, size = [], brackets
            row_bytes = len(json.dumps(row))
        batch.append(row)
        size += row_bytes
    if batch:
        yield batch

