"""SQLFluff lint of the authoritative DDL and every reference_sql
(scope doc Section 7.1 / 9.4).

Hard gate - sqlfluff is a pinned requirement, not optional. Layout rules
(line length, trailing newline, indentation) are excluded for reference_sql
because it is STORED as one collapsed line in the contract CSV - that is a
serialization choice, not the authored form. schema/ddl.sql IS linted with
layout rules on (only the house-style rules are excluded), against the
project .sqlfluff, exactly as `sqlfluff lint schema/ddl.sql` would.

AL01 (implicit table aliasing) and RF04 (schema column names that are also
keywords) are house style - see .sqlfluff.
"""

import pathlib

import sqlfluff
from sqlfluff.core import FluffConfig

REPO = pathlib.Path(__file__).resolve().parent.parent
DDL = REPO / "schema" / "ddl.sql"

HOUSE_STYLE = ["AL01", "RF04"]
EXCLUDE = HOUSE_STYLE + [
    "LT01",
    "LT02",
    "LT05",
    "LT08",
    "LT09",
    "LT12",  # layout - N/A to a collapsed line
]


def _lint(sql: str, exclude, config=None):
    return [
        v
        for v in sqlfluff.lint(sql, dialect="ansi", exclude_rules=exclude, config=config)
        if not v.get("warning")
    ]


def test_authoritative_ddl_is_sqlfluff_clean():
    """schema/ddl.sql must pass `sqlfluff lint schema/ddl.sql` (project config)."""
    cfg = FluffConfig.from_path(str(REPO))
    v = _lint(DDL.read_text(), HOUSE_STYLE, config=cfg)
    assert not v, [(x["code"], x["line_no"]) for x in v]


def test_every_release_reference_sql_is_clean(pairs):
    seen, failures = set(), []
    for r in pairs:
        s = r["reference_sql"]
        if s in seen:
            continue
        seen.add(s)
        v = _lint(s, EXCLUDE)
        if v:
            failures.append((r["natural_language_question"][:55], [x["code"] for x in v]))
    assert not failures, failures
